# -*- coding: utf-8 -*-
"""
Linopy constraints module.
This module contains implementations for the Constraint{s} class.
"""

import re
from dataclasses import dataclass
from typing import Any, Sequence, Union

import dask
import numpy as np
import pandas as pd
import xarray as xr
from numpy import asarray
from scipy.sparse import coo_matrix
from xarray import DataArray, Dataset

from linopy.common import _merge_inplace, replace_by_map


class Constraint(DataArray):
    """
    Constraint container for storing constraint labels.

    The Constraint class is a subclass of xr.DataArray hence most xarray functions
    can be applied to it.
    """

    __slots__ = ("_cache", "_coords", "_indexes", "_name", "_variable", "model")

    def __init__(self, *args, **kwargs):

        # workaround until https://github.com/pydata/xarray/pull/5984 is merged
        if isinstance(args[0], DataArray):
            da = args[0]
            args = (da.data, da.coords)
            kwargs.update({"attrs": da.attrs, "name": da.name})

        self.model = kwargs.pop("model", None)
        super().__init__(*args, **kwargs)
        assert self.name is not None, "Constraint data does not have a name."

    # We have to set the _reduce_method to None, in order to overwrite basic
    # reduction functions as `sum`. There might be a better solution (?).
    _reduce_method = None

    def __repr__(self):
        """Get the string representation of the constraints."""
        data_string = (
            "Constraint labels:\n" + self.to_array().__repr__().split("\n", 1)[1]
        )
        extend_line = "-" * len(self.name)
        return (
            f"Constraint '{self.name}':\n"
            f"--------------{extend_line}\n\n"
            f"{data_string}"
        )

    def _repr_html_(self):
        """Get the html representation of the variables."""
        # return self.__repr__()
        data_string = self.to_array()._repr_html_()
        data_string = data_string.replace("xarray.DataArray", "linopy.Constraint")
        return data_string

    def to_array(self):
        """Convert the variable array to a xarray.DataArray."""
        return DataArray(self)

    # would like to have this as a property, but this does not work apparently
    def get_coeffs(self):
        """
        Get the left-hand-side coefficients of the constraint.
        The function raises an error in case no model is set as a reference.
        """
        if self.model is None:
            raise AttributeError("No reference model is assigned to the variable.")
        return self.model.constraints.coeffs[self.name]

    def get_vars(self):
        """
        Get the left-hand-side variables of the constraint.
        The function raises an error in case no model is set as a reference.
        """
        if self.model is None:
            raise AttributeError("No reference model is assigned to the variable.")
        return self.model.constraints.vars[self.name]

    def get_sign(self):
        """
        Get the sign of the constraint.
        The function raises an error in case no model is set as a reference.
        """
        if self.model is None:
            raise AttributeError("No reference model is assigned to the variable.")
        return self.model.constraints.sign[self.name]

    def get_rhs(self):
        """
        Get the right-hand-side constant of the constraint.
        The function raises an error in case no model is set as a reference.
        """
        if self.model is None:
            raise AttributeError("No reference model is assigned to the variable.")
        return self.model.constraints.rhs[self.name]


@dataclass(repr=False)
class Constraints:
    """
    A constraint container used for storing multiple constraint arrays.
    """

    labels: Dataset = Dataset()
    coeffs: Dataset = Dataset()
    vars: Dataset = Dataset()
    sign: Dataset = Dataset()
    rhs: Dataset = Dataset()
    blocks: Dataset = Dataset()
    model: Any = None  # Model is not defined due to circular imports

    dataset_attrs = ["labels", "coeffs", "vars", "sign", "rhs"]
    dataset_names = [
        "Labels",
        "Left-hand-side coefficients",
        "Left-hand-side variables",
        "Signs",
        "Right-hand-side constants",
    ]

    def __repr__(self):
        """Return a string representation of the linopy model."""
        r = "linopy.model.Constraints"
        line = "-" * len(r)
        r += f"\n{line}\n\n"
        # matches string between "Data variables" and "Attributes"/end of string
        coordspattern = r"(?s)(?<=\<xarray\.Dataset\>\n).*?(?=Data variables:)"
        datapattern = r"(?s)(?<=Data variables:).*?(?=($|\nAttributes))"
        for (k, K) in zip(self.dataset_attrs, self.dataset_names):
            orig = getattr(self, k).__repr__()
            if k == "labels":
                r += re.search(coordspattern, orig).group() + "\n"
            data = re.search(datapattern, orig).group()
            # drop first line which includes counter for long ds
            data = data.split("\n", 1)[1]
            line = "-" * (len(K) + 1)
            r += f"{K}:\n{data}\n\n"
        return r

    def __getitem__(
        self, names: Union[str, Sequence[str]]
    ) -> Union[Constraint, "Constraints"]:
        if isinstance(names, str):
            return Constraint(self.labels[names], model=self.model)

        return self.__class__(
            self.labels[names],
            self.coeffs[names],
            self.vars[names],
            self.sign[names],
            self.rhs[names],
            self.model,
        )

    def __iter__(self):
        return self.labels.__iter__()

    _merge_inplace = _merge_inplace

    def add(
        self,
        name,
        labels: DataArray,
        coeffs: DataArray,
        vars: DataArray,
        sign: DataArray,
        rhs: DataArray,
    ):
        """Add constraint `name`."""
        self._merge_inplace("labels", labels, name, fill_value=-1)
        self._merge_inplace("coeffs", coeffs, name)
        self._merge_inplace("vars", vars, name, fill_value=-1)
        self._merge_inplace("sign", sign, name)
        self._merge_inplace("rhs", rhs, name)

    def remove(self, name):
        """Remove constraint `name` from the constraints."""
        for attr in self.dataset_attrs:
            setattr(self, attr, getattr(self, attr).drop_vars(name))

    @property
    def coefficientrange(self):
        """Coefficient range of the constraint."""
        return (
            xr.concat(
                [self.coeffs.min(), self.coeffs.max()],
                dim=pd.Index(["min", "max"]),
            )
            .to_dataframe()
            .T
        )

    @property
    def ncons(self):
        """
        Get the number all constraints which were at some point added to the model.
        These also include constraints with missing labels.
        """
        return self.model.ncons

    @property
    def inequalities(self):
        "Get the subset of constraints which are purely inequalities."
        return self[[n for n, s in self.sign.items() if s in ("<=", ">=")]]

    @property
    def equalities(self):
        "Get the subset of constraints which are purely equalities."
        return self[[n for n, s in self.sign.items() if s in ("=", "==")]]

    def get_blocks(self, block_map):
        """
        Get a dataset of same shape as constraints.labels with block values.

        Let N be the number of blocks.
        The following cases are considered:
            * where are all vars are -1, the block is -1
            * where are all vars are 0, the block is 0
            * where all vars are n, the block is n
            * where vars are n or 0 (both present), the block is n
            * N+1 otherwise

        """
        N = block_map.max()
        var_blocks = replace_by_map(self.vars, block_map)
        res = xr.full_like(self.labels, N + 1, dtype=block_map.dtype)

        for name, entries in var_blocks.items():
            term_dim = f"{name}_term"

            not_zero = entries != 0
            not_missing = entries != -1
            for n in range(N + 1):
                not_n = entries != n
                mask = not_n & not_zero & not_missing
                res[name] = res[name].where(mask.any(term_dim), n)

            res[name] = res[name].where(not_missing.any(term_dim), -1)
            res[name] = res[name].where(not_zero.any(term_dim), 0)

        self.blocks = res
        self.var_blocks = var_blocks
        return self.blocks

    def iter_ravel(self, key, broadcast_like="labels", filter_missings=False):
        """
        Create an generator which iterates over all arrays in `key` and flattens them.

        Parameters
        ----------
        key : str/Dataset
            Key to be iterated over. Optionally pass a dataset which is
            broadcastable to `broadcast_like`.
        broadcast_like : str, optional
            Name of the dataset to which the input data in `key` is aligned to.
            The default is "labels".
        filter_missings : bool, optional
            Filter out values where `broadcast_like` data is -1.
            The default is False.


        Yields
        ------
        flat : np.array/dask.array

        """
        if isinstance(key, str):
            ds = getattr(self, key)
        elif isinstance(key, xr.Dataset):
            ds = key
        else:
            raise TypeError("Argument `key` must be of type string or xarray.Dataset")

        for name, values in getattr(self, broadcast_like).items():

            broadcasted = ds[name].broadcast_like(values)
            if values.chunks is not None:
                broadcasted = broadcasted.chunk(values.chunks)

            flat = broadcasted.data.ravel()
            if filter_missings:
                flat = flat[values.data.ravel() != -1]
            yield flat

    def ravel(self, key, broadcast_like="labels", filter_missings=False, compute=False):
        """
        Ravel and concate all arrays in `key` while aligning to `broadcast_like`.

        Parameters
        ----------
        key : str/Dataset
            Key to be iterated over. Optionally pass a dataset which is
            broadcastable to `broadcast_like`.
        broadcast_like : str, optional
            Name of the dataset to which the input data in `key` is aligned to.
            The default is "labels".
        filter_missings : bool, optional
            Filter out values where `broadcast_like` data is -1.
            The default is False.
        compute : bool, optional
            Whether to compute lazy data. The default is False.

        Returns
        -------
        flat
            One dimensional data with all values in `key`.

        """
        res = list(self.iter_ravel(key, broadcast_like, filter_missings))
        res = np.concatenate(res)
        if compute:
            return dask.compute(res)[0]
        else:
            return res

    def to_matrix(self):
        """
        Construct a constraint matrix in sparse format.

        Missing values, i.e. -1 in labels and vars, are ignored filtered out.
        """
        shape = (self.model.ncons, self.model.nvars)
        keys = ["coeffs", "labels", "vars"]
        data, rows, cols = [self.ravel(k, broadcast_like="vars") for k in keys]
        non_missing = (rows != -1) & (cols != -1)
        data = asarray(data[non_missing])
        rows = asarray(rows[non_missing])
        cols = asarray(cols[non_missing])
        return coo_matrix((data, (rows, cols)), shape=shape)