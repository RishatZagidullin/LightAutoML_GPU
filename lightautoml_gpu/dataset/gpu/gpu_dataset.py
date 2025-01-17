"""Internal representation of dataset in cudf formats."""

from copy import copy, deepcopy
from typing import Any
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import TypeVar
from typing import Union
from typing import Callable

import warnings

import numpy as np
import pandas as pd

import cudf
import cupy as cp
import dask_cudf
from cudf.core.dataframe import DataFrame
from cudf.core.series import Series
from cupyx.scipy import sparse as sparse_cupy
from dask_cudf.core import DataFrame as DataFrame_dask
from dask_cudf.core import Series as Series_dask

from lightautoml_gpu.dataset.base import IntIdx
from lightautoml_gpu.dataset.base import LAMLDataset
from lightautoml_gpu.dataset.base import LAMLColumn
from lightautoml_gpu.dataset.base import RolesDict
from lightautoml_gpu.dataset.base import array_attr_roles
from lightautoml_gpu.dataset.base import valid_array_attributes

from lightautoml_gpu.dataset.np_pd_dataset import CSRSparseDataset
from lightautoml_gpu.dataset.np_pd_dataset import NumpyDataset
from lightautoml_gpu.dataset.np_pd_dataset import PandasDataset

from lightautoml_gpu.dataset.roles import ColumnRole
from lightautoml_gpu.dataset.roles import DropRole
from lightautoml_gpu.dataset.roles import NumericRole
from lightautoml_gpu.tasks.base import Task

NpFeatures = Union[Sequence[str], str, None]
NpRoles = Union[Sequence[ColumnRole], ColumnRole, RolesDict, None]
DenseSparseArray = Union[cp.ndarray, sparse_cupy.csr_matrix]
FrameOrSeries = Union[DataFrame, Series]
FrameOrSeries_dask = Union[DataFrame_dask, Series_dask]
Dataset = TypeVar("Dataset", bound=LAMLDataset)
RowSlice = Optional[Union[Sequence[int], Sequence[bool]]]
ColSlice = Optional[Union[Sequence[str], str]]


class CupyDataset(NumpyDataset):
    """Dataset that contains info in cp.ndarray format.

    Create dataset from numpy/cupy arrays.

        Args:
            data: 2d array of features.
            features: Features names.
            roles: Roles specifier.
            task: Task specifier.
            **kwargs: Named attributes like target, group etc ..

        Note:
            For different type of parameter feature there is different behavior:

                - list, should be same len as data.shape[1]
                - None - automatic set names like feat_0, feat_1 ...
                - Prefix - automatic set names like Prefix_0, Prefix_1 ...

            For different type of parameter feature there is different behavior:

                - list, should be same len as data.shape[1].
                - None - automatic set NumericRole(np.float32).
                - ColumnRole - single role.
                - dict.

    """

    _dataset_type = "CupyDataset"

    def __init__(
        self,
        data: Optional[DenseSparseArray],
        features: NpFeatures = (),
        roles: NpRoles = None,
        task: Optional[Task] = None,
        **kwargs: np.ndarray
    ):

        self._initialize(task, **kwargs)
        for k in kwargs:
            self.__dict__[k] = cp.asarray(kwargs[k])
        if data is not None:
            self.set_data(data, features, roles)

    def _check_dtype(self):
        """Check if dtype in ``.set_data`` is ok and cast if not.

        Raises:
            AttributeError: If there is non-numeric type in dataset.

        """
        dtypes = list(set([i.dtype for i in self.roles.values()]))
        self.dtype = cp.find_common_type(dtypes, [])

        for f in self.roles:
            self._roles[f].dtype = self.dtype

        assert cp.issubdtype(
            self.dtype, cp.number
        ), "Support only numeric types in Cupy dataset."

        if self.data.dtype != self.dtype:
            self.data = self.data.astype(self.dtype)

    def set_data(
        self, data: DenseSparseArray, features: NpFeatures = (), roles: NpRoles = None
    ):
        """Inplace set data, features, roles for empty dataset.

        Args:
            data: 2d cp.array of features.
            features: features names.
            roles: Roles specifier.

        Note:
            For different type of parameter feature there is different behavior:

                - List, should be same len as data.shape[1]
                - None - automatic set names like feat_0, feat_1 ...
                - Prefix - automatic set names like Prefix_0, Prefix_1 ...

            For different type of parameter feature there is different behavior:

                - List, should be same len as data.shape[1].
                - None - automatic set NumericRole(cp.float32).
                - ColumnRole - single role.
                - dict.

        """
        assert (
            data is None or type(data) is cp.ndarray
        ), "Cupy dataset support only cp.ndarray features"
        super(CupyDataset.__bases__[0], self).set_data(data, features, roles)
        self._check_dtype()

    @staticmethod
    def _hstack(datasets: Sequence[cp.ndarray]) -> cp.ndarray:
        """Concatenate function for cupy arrays.

        Args:
            datasets: Sequence of cp.ndarray.

        Returns:
            Stacked features array.

        """
        return cp.hstack([data for data in datasets if len(data) > 0])

    def to_numpy(self) -> NumpyDataset:
        """Convert to numpy.

        Returns:
            Numpy dataset
        """

        assert all(
            [self.roles[x].name == "Numeric" for x in self.features]
        ), "Only numeric data accepted in numpy dataset"

        data = None if self.data is None else cp.asnumpy(self.data)

        roles = self.roles
        features = self.features
        # target and etc ..
        params = dict(
            ((x, cp.asnumpy(self.__dict__[x])) for x in self._array_like_attrs)
        )
        task = self.task

        return NumpyDataset(data, features, roles, task, **params)

    def to_cupy(self) -> "CupyDataset":
        """Empty method to convert to cupy.

        Returns:
            Same CupyDataset.
        """

        return self

    def to_pandas(self) -> PandasDataset:
        """Convert to PandasDataset.

        Returns:
            Same dataset in PandasDataset format.
        """

        return self.to_cudf().to_pandas()

    def to_csr(self) -> CSRSparseDataset:
        """Convert to csr.

        Returns:
            Same dataset in CSRSparseDatatset format.
        """

        return self.to_numpy().to_csr()

    def to_cudf(self) -> "CudfDataset":
        """Convert to CudfDataset.

        Returns:
            Same dataset in CudfDataset format.
        """
        data = None if self.data is None else cudf.DataFrame()
        if data is not None:
            data_gpu = cudf.DataFrame()
            for i, col in enumerate(self.features):
                data_gpu[col] = cudf.Series(self.data[:, i], nan_as_null=False)
            data = data_gpu
        roles = self.roles
        # target and etc ..
        params = dict(
            (
                (x, cudf.Series(self.__dict__[x]) if len(self.__dict__[x].shape) == 1 else cudf.DataFrame(self.__dict__[x]))
                for x in self._array_like_attrs
            )
        )
        task = self.task

        return CudfDataset(data, roles, task, **params)

    def to_daskcudf(self, nparts: int = 1, index_ok: bool = True) -> "DaskCudfDataset":
        """Convert dataset to daskcudf.

        Returns:
            Same dataset in DaskCudfDataset format

        """
        return self.to_cudf().to_daskcudf(nparts, index_ok)

    def to_sparse_gpu(self) -> "CupySparseDataset":
        """Convert to cupy-based csr.
        Returns:
            Same dataset in CupySparseDataset format (CSR).
        """
        assert all(
            [self.roles[x].name == "Numeric" for x in self.features]
        ), "Only numeric data accepted in sparse dataset"
        data = None if self.data is None else sparse_cupy.csr_matrix(self.data)

        roles = self.roles
        features = self.features
        # target and etc ..
        params = dict(((x, self.__dict__[x]) for x in self._array_like_attrs))
        task = self.task

        return CupySparseDataset(data, features, roles, task, **params)

    @staticmethod
    def from_dataset(dataset: Dataset) -> "CupyDataset":
        """Convert random dataset to cupy.

        Returns:
            Cupy dataset.

        """
        return dataset.to_cupy()


class CupySparseDataset(CupyDataset):
    """Dataset that contains sparse features on GPU and cp.ndarray targets.
    Create dataset from csr_matrix.
        Args:
            data: csr_matrix of features.
            features: Features names.
            roles: Roles specifier.
            task: Task specifier.
            **kwargs: Named attributes like target, group etc ..
        Note:
            For different type of parameter feature there is different behavior:
                - list, should be same len as data.shape[1]
                - None - automatic set names like feat_0, feat_1 ...
                - Prefix - automatic set names like Prefix_0, Prefix_1 ...
            For different type of parameter feature there is different behavior:
                - list, should be same len as data.shape[1].
                - None - automatic set NumericRole(cp.float32).
                - ColumnRole - single role.
                - dict.
    """

    _dataset_type = "CupySparseDataset"

    @staticmethod
    def _get_cols(data: Any, k: Any):
        """Not implemented."""
        raise NotImplementedError

    @staticmethod
    def _set_col(data: Any, k: Any, val: Any):
        """Not implemented."""
        raise NotImplementedError

    def to_pandas(self) -> Any:
        """Not implemented."""
        raise NotImplementedError

    def to_cupy(self) -> "CupyDataset":
        """Convert to CupyDataset.
        Returns:
            CupyDataset.
        """
        # check for empty
        data = None if self.data is None else self.data.toarray()
        assert (
            type(data) is cp.ndarray
        ), "Data conversion failed! Check types of datasets."
        roles = self.roles
        features = self.features
        # target and etc ..
        params = dict(((x, self.__dict__[x]) for x in self._array_like_attrs))
        task = self.task

        return CupyDataset(data, features, roles, task, **params)

    @property
    def shape(self) -> Tuple[Optional[int], Optional[int]]:
        """Get size of 2d feature matrix.
        Returns:
            tuple of 2 elements.
        """
        rows, cols = None, None
        try:
            rows, cols = self.data.shape
        except TypeError:
            if len(self._array_like_attrs) > 0:
                rows = len(self.__dict__[self._array_like_attrs[0]])
        return rows, cols

    @staticmethod
    def _hstack(
        datasets: Sequence[Union[sparse_cupy.csr_matrix, cp.ndarray]]
    ) -> sparse_cupy.csr_matrix:
        """Concatenate function for sparse and numpy arrays.
        Args:
            datasets: Sequence of csr_matrix or np.ndarray.
        Returns:
            Sparse matrix.
        """
        return sparse_cupy.hstack(datasets, format="csr")

    def __init__(
        self,
        data: Optional[DenseSparseArray],
        features: NpFeatures = (),
        roles: NpRoles = None,
        task: Optional[Task] = None,
        **kwargs: np.ndarray
    ):
        self._initialize(task, **kwargs)
        if data is not None:
            self.set_data(data, features, roles)

    def set_data(
        self, data: DenseSparseArray, features: NpFeatures = (), roles: NpRoles = None
    ):
        """Inplace set data, features, roles for empty dataset.
        Args:
            data: csr_matrix of features.
            features: features names.
            roles: Roles specifier.
        Note:
            For different type of parameter feature there is different behavior:
                - list, should be same len as data.shape[1]
                - None - automatic set names like feat_0, feat_1 ...
                - Prefix - automatic set names like Prefix_0, Prefix_1 ...
            For different type of parameter feature there is different behavior:
                - list, should be same len as data.shape[1].
                - None - automatic set NumericRole(cp.float32).
                - ColumnRole - single role.
                - dict.
        """
        assert (
            data is None or type(data) is sparse_cupy.csr_matrix
        ), "CSRSparseDataset support only csr_matrix features"
        LAMLDataset.set_data(self, data, features, roles)
        self._check_dtype()

    @staticmethod
    def from_dataset(dataset: Dataset) -> "CSRSparseDataset":
        """Convert dataset to sparse dataset.
        Returns:
            Dataset in sparse form.
        """
        assert (
            type(dataset) in DenseSparseArray
        ), "Only Numpy/Cupy based datasets can be converted to sparse datasets!"
        return dataset.to_sparse_gpu()


class CudfDataset(PandasDataset):
    """Dataset that contains `cudf.core.dataframe.DataFrame` features and
       ` cudf.core.series.Series` targets.

    Create dataset from `cudf.core.dataframe.DataFrame` and
           ` cudf.core.series.Series`

        Args:
            data: Table with features.
            features: features names.
            roles: Roles specifier.
            task: Task specifier.
            **kwargs: Series, array like attrs target, group etc...

    """

    _dataset_type = "CudfDataset"

    def __init__(
        self,
        data: Optional[DataFrame] = None,
        roles: Optional[RolesDict] = None,
        task: Optional[Task] = None,
        **kwargs: Series
    ):
        if roles is None:
            roles = {}
        # parse parameters
        # check if target, group etc .. defined in roles
        for f in roles:
            for k, r in zip(valid_array_attributes, array_attr_roles):
                if roles[f].name == r:
                    kwargs[k] = data[f].reset_index(drop=True)
                    roles[f] = DropRole()
        self._initialize(task, **kwargs)
        if data is not None:
            self.set_data(data, None, roles)

    @property
    def roles(self) -> RolesDict:
        """Roles dict."""
        return copy(self._roles)

    @roles.setter
    def roles(self, val: NpRoles):
        """Define how to set roles.

        Args:
            val: Roles.

        Note:
            There is different behavior for different type of val parameter:

                - `List` - should be same len as ``data.shape[1]``.
                - `None` - automatic set ``NumericRole(np.float32)``.
                - ``ColumnRole`` - single role for all.
                - ``dict``.

        """
        if type(val) is dict:
            self._roles = dict(((x, val[x]) for x in self.features))
        elif type(val) is list:
            self._roles = dict(zip(self.features, val))
        else:
            role = NumericRole(np.float32) if val is None else val
            self._roles = dict(((x, role) for x in self.features))

    def set_data(self, data: DataFrame, features: None, roles: RolesDict):
        """Inplace set data, features, roles for empty dataset.

        Args:
            data: Table with features.
            features: `None`, just for same interface.
            roles: Dict with roles.

        """
        super(CudfDataset.__bases__[0], self).set_data(data, features, roles)
        self._check_dtype()

    def _check_dtype(self):
        """Check if dtype in .set_data is ok and cast if not."""
        date_columns = []

        self.dtypes = {}
        for f in self.roles:
            if self.roles[f].name == "Datetime":
                date_columns.append(f)
            else:
                self.dtypes[f] = self.roles[f].dtype

        try:
            self.data = self.data.astype(self.dtypes)
        except:
            pass

        # handle dates types
        self.data = self._convert_datetime(self.data, date_columns)

        for i in date_columns:
            self.dtypes[i] = np.datetime64

    def _convert_datetime(self, data: DataFrame, date_cols: List[str]) -> DataFrame:
        """Convert the listed columns of the DataFrame to DateTime type
           according to the defined roles.

        Args:
            data: Table with features.
            date_cols: Table column names that need to be converted.

        Returns:
            Data converted to datetime format from roles.

        """
        for i in date_cols:
            dt_role = self.roles[i]
            if not data.dtypes[i] is np.datetime64:
                if dt_role.unit is None:
                    data[i] = cudf.to_datetime(
                        data[i], format=dt_role.format, origin=dt_role.origin, cache=True
                    )
                else:
                    data[i] = cudf.to_datetime(
                        data[i],
                        format=dt_role.format,
                        unit=dt_role.unit,
                        origin=dt_role.origin,
                        cache=True,
                    )
        return data

    @staticmethod
    def _hstack(datasets: Sequence[DataFrame]) -> DataFrame:
        """Define how to concat features arrays.

        Args:
            datasets: Sequence of tables.

        Returns:
            concatenated table.

        """
        return cudf.concat(datasets, axis=1)

    @staticmethod
    def _get_rows(data: DataFrame, k: IntIdx) -> FrameOrSeries:
        """Define how to get rows slice.

        Args:
            data: Table with data.
            k: Sequence of `int` indexes or `int`.

        Returns:
            Sliced rows.

        """
        return data.iloc[k]

    @staticmethod
    def _get_cols(data: DataFrame, k: IntIdx) -> FrameOrSeries:
        """Define how to get cols slice.

        Args:
            data: Table with data.
            k: Sequence of `int` indexes or `int`

        Returns:
           Sliced cols.

        """
        return data.iloc[:, k]

    @classmethod
    def _get_2d(cls, data: DataFrame, k: Tuple[IntIdx, IntIdx]) -> FrameOrSeries:
        """Define 2d slice of table.

        Args:
            data: Table with data.
            k: Sequence of `int` indexes or `int`.

        Returns:
            2d sliced table.

        """
        rows, cols = k

        return data.iloc[rows, cols]

    @staticmethod
    def _set_col(data: DataFrame, k: int, val: Union[Series, np.ndarray]):
        """Inplace set column value to `cudf.DataFrame`.

        Args:
            data: Table with data.
            k: Column index.
            val: Values to set.

        """
        data.iloc[:, k] = val

    def to_cupy(self) -> CupyDataset:
        """Convert to class:`NumpyDataset`.

        Returns:
            Same dataset in class:`NumpyDataset` format.

        """
        # check for empty
        roles = self.roles
        features = self.features
        # target and etc ..
        params = dict(((x, self.__dict__[x].values) for x in self._array_like_attrs))
        task = self.task

        if self.data is None:
            return CupyDataset(None, features, roles, task, **params)

        return CupyDataset(cp.asarray(self.data.fillna(cp.nan).values),
                           features, roles, task, **params)

    def to_numpy(self) -> NumpyDataset:
        """Convert to class:`NumpyDataset`.

        Returns:
            Same dataset in class:`NumpyDataset` format.

        """

        return self.to_cupy().to_numpy()

    def to_pandas(self) -> PandasDataset:
        """Convert dataset to pandas.

        Returns:
            Same dataset in PandasDataset format

        """
        data = self.data.to_pandas()
        roles = self.roles
        task = self.task

        params = dict(
            (
                (x, pd.Series(cp.asnumpy(self.__dict__[x].values))
                 if len(self.__dict__[x].shape) == 1 else pd.DataFrame(cp.asnumpy(self.__dict__[x].values))
                 ) for x in self._array_like_attrs
            )
        )

        return PandasDataset(data, roles, task, **params)

    def to_cudf(self) -> "CudfDataset":
        """Empty method to return self

        Returns:
            self
        """

        return self

    def to_sparse_gpu(self) -> "CupySparseDataset":

        return self.to_cupy().to_sparse_gpu()

    def to_daskcudf(self, nparts: int = 1, index_ok=True) -> "DaskCudfDataset":
        """Convert dataset to daskcudf.

        Returns:
            Same dataset in DaskCudfDataset format

        """
        data = None
        if self.data is not None:
            data = dask_cudf.from_cudf(self.data, npartitions=nparts)
        roles = self.roles
        task = self.task

        params = dict(
            (
                (x, dask_cudf.from_cudf(self.__dict__[x], npartitions=nparts))
                for x in self._array_like_attrs
            )
        )

        return DaskCudfDataset(data, roles, task, index_ok=index_ok, **params)

    @staticmethod
    def from_dataset(dataset: Dataset) -> "CudfDataset":
        """Convert random dataset (if it has .to_cudf() member) to cudf dataset.

        Returns:
            Converted to cudf dataset.

        """
        return dataset.to_cudf()


class SeqCudfDataset(CudfDataset):
    """Sequential Dataset, that contains info in cudf.DataFrame format."""

    _dataset_type = "SeqCudfDatset"

    def _initialize(self, task: Optional[Task], **kwargs: Any):
        """Initialize empty dataset with task and array like attributes.

        Args:
            task: Task name for dataset.
            **kwargs: 1d arrays like attrs like target, group etc.

        """
        super()._initialize(task, **kwargs)
        self._idx = None

    @property
    def idx(self) -> Any:
        """Get idx attribute.

        Returns:
            Any, array like or ``None``.

        """
        return self._idx

    @idx.setter
    def idx(self, val: Any):
        """Set idx array or ``None``.

        Args:
            val: Some idx or ``None``.

        """
        self._idx = val

    def __init__(
        self,
        data: Optional[DenseSparseArray],
        features: NpFeatures = (),
        roles: NpRoles = None,
        idx: List = (),
        task: Optional[Task] = None,
        name: Optional[str] = "seq",
        scheme: Optional[dict] = None,
        **kwargs: Series
    ):
        self.name = name
        if scheme is not None:
            self.scheme = scheme
        else:
            self.scheme = {}

        self._initialize(task, **kwargs)
        if data is not None:
            self.set_data(data, roles, idx)

    def set_data(self, data: DenseSparseArray, roles: NpRoles = None, idx: Optional[List] = None):

        super().set_data(data, None, roles)
        if idx is None:
            idx = np.arange(len(data)).reshape(-1, 1)
        self.idx = idx
        self._check_dtype()

    def __len__(self) -> int:
        return len(self.idx)

    def _get_cols_idx(self, columns: Union[Sequence[str], str]) -> Union[Sequence[int], int]:
        """Get numeric index of columns by column names.

        Args:
            columns: sequence of columns of single column.

        Returns:
            sequence of int indexes or single int.

        """
        if type(columns) is str:
            idx = self.data.columns.get_loc(columns)

        else:
            idx = self.data.columns.get_indexer(columns)

        return idx

    def __getitem__(self, k: Tuple[RowSlice, ColSlice]) -> Union["LAMLDataset", LAMLColumn]:
        """Select a subset of dataset.

        Define how to slice a dataset
        in way ``dataset[[1, 2, 3...], ['feat_0', 'feat_1'...]]``.
        Default behavior based on ``._get_cols``, ``._get_rows``, ``._get_2d``.

        Args:
            k: First element optional integer columns indexes,
                second - optional feature name or list of features names.

        Returns:
            Subset.

        """
        if type(k) is tuple:
            rows, cols = k
            if isinstance(cols, str):
                cols = [cols]
        else:
            rows = k
            cols = None

        is_slice = False
        if isinstance(rows, slice):
            is_slice = True

        rows = [rows] if isinstance(rows, int) else np.arange(self.__len__()) if isinstance(rows, slice) else rows
        temp_idx = self.idx[rows]
        rows = []
        idx_new = []
        _c = 0
        for i in temp_idx:
            rows.extend(list(i))
            idx_new.append(list(np.arange(len(i)) + _c))
            _c += len(i)
        idx_new = np.array(idx_new, dtype=object)

        rows = np.array(sorted(list(set(rows))))

        if is_slice:
            idx_new = self.idx
            rows = np.arange(len(self.data))
        else:
            warnings.warn(
                "Resulted sequential dataset may have different structure. It's not recommended to slice new dataset (GPU)"
            )

        # case when columns are defined
        if cols is not None:
            idx = self._get_cols_idx(cols)
            data = self._get_2d(self.data, (rows, idx))

            # case of multiple columns - return LAMLDataset
            roles = dict(((x, self.roles[x]) for x in self.roles if x in cols))
        else:
            roles = self.roles
            data = self._get_rows(self.data, rows)

        # case when rows are defined
        if rows is None:
            dataset = self.empty()
        else:
            dataset = copy(self)
            params = dict(((x, self._get_rows(self.__dict__[x], rows)) for x in self._array_like_attrs))
            dataset._initialize(self.task, **params)

        dataset.set_data(data, roles, idx=idx_new)

        return dataset

    def get_first_frame(self, k: Tuple[RowSlice, ColSlice] = None) -> CudfDataset:
        """Select a subset of dataset with only first elements of the sequential features.

        Define how to slice a dataset in way ``dataset[[1, 2, 3...], ['feat_0', 'feat_1'...]]``.
        Default behavior based on ``._get_cols_idx``, ``._get_slice``.

        Args:
            k: First element optional integer columns indexes,
                second - optional feature name or list of features names.

        Returns:
            respective Dataset with first elements of the sequential features.

        """
        self._check_dtype()
        if k is None:
            k = slice(None, None, None)

        if type(k) is tuple:
            rows, cols = k
            if isinstance(cols, str):
                cols = [cols]
        else:
            rows = k
            cols = None

        rows = [rows] if isinstance(rows, int) else np.arange(self.__len__()) if isinstance(rows, slice) else rows

        # case when columns are defined
        if cols is not None:
            idx = self._get_cols_idx(cols)
            roles = dict(((x, self.roles[x]) for x in self.roles if x in cols))
        else:
            roles = self.roles
            idx = self._get_cols_idx(self.data.columns)

        data = self.data
        if type(self.idx == np.ndarray) and (np.isnan(self.idx).any()):
            data = cudf.concat([data, cudf.DataFrame([cp.nan])])
            self.idx = np.nan_to_num(self.idx, nan=[-1])
        first_frame_idx = [self.idx[i][0] for i in rows]

        data = self._get_slice(data, (first_frame_idx, idx))

        if rows is None:
            dataset = CudfDataset(None, deepcopy(roles), task=self.task)
        else:
            dataset = CudfDataset(data, deepcopy(roles), task=self.task)
        return dataset

    def apply_func(self, k: Tuple[RowSlice, ColSlice] = None, func: Callable = None) -> cudf.DataFrame:
        """Apply function to each sequence.

        Args:
            k: First element optional integer columns indexes,
                second - optional feature name or list of features names.
            func: any callable function

        Returns:
            output cudf.DataFrame

        """
        self._check_dtype()
        if k is None:
            k = slice(None, None, None)

        if type(k) is tuple:
            rows, cols = k
            if isinstance(cols, str):
                cols = [cols]
        else:
            rows = k
            cols = None

        rows = [rows] if isinstance(rows, int) else np.arange(self.__len__()) if isinstance(rows, slice) else rows

        # case when columns are defined
        if cols is not None:

            # case when seqs have different shape, return array with arrays
            data = []
            _d = self.data[cols].values
            for row in rows:
                data.append(func(_d[self.idx[row]]))
        else:
            data = []
            _d = self.data.values
            for row in rows:
                data.append(func(_d[self.idx[row]]))

        return cudf.DataFrame(data)

    def _get_slice(self, data: cudf.DataFrame, k: Tuple[RowSlice, ColSlice]) -> cudf.DataFrame:
        """Get 2d slice.

        Args:
            data: Data.
            k: Tuple of integer sequences.

        Returns:
            2d slice.

        """
        rows, cols = k
        if isinstance(data, cudf.DataFrame):
            return data.iloc[rows, cols]
        else:
            raise TypeError("wrong data type for _get_slice() in " + self.__class__.__name__)

    def to_cudf(self) -> "CudfDataset":
        """Convert to plain CudfDataset.

        Returns:
            Same dataset in CudfDataset format without sequential features.

        """
        # check for empty case
        data = None if self.data is None else cudf.DataFrame(self.data, columns=self.features)
        roles = self.roles
        # target and etc ..
        params = dict(((x, cudf.Series(self.__dict__[x])) for x in self._array_like_attrs))
        task = self.task

        return CudfDataset(data, roles, task, **params)

    @classmethod
    def concat(cls, datasets: Sequence["LAMLDataset"]) -> "LAMLDataset":
        """Concat multiple dataset.

        Default behavior - takes empty dataset from datasets[0]
        and concat all features from others.

        Args:
            datasets: Sequence of datasets.

        Returns:
            Concated dataset.

        """
        for check in cls._concat_checks:
            check(datasets)

        idx = datasets[0].idx
        dataset = datasets[0].empty()
        data = []
        features = []
        roles = {}

        atrs = set(dataset._array_like_attrs)
        for ds in datasets:
            data.append(ds.data)
            features.extend(ds.features)
            roles = {**roles, **ds.roles}
            for atr in ds._array_like_attrs:
                if atr not in atrs:
                    dataset._array_like_attrs.append(atr)
                    dataset.__dict__[atr] = ds.__dict__[atr]
                    atrs.update({atr})

        data = cls._hstack(data)
        dataset.set_data(data, roles, idx=idx)

        return dataset


class DaskCudfDataset(CudfDataset):
    """Dataset that contains `dask_cudf.core.DataFrame` features and
       `dask_cudf.Series` or `dask_cudf.DataFrame` targets.

    Dataset that contains `dask_cudf.core.DataFrame` and
       `dask_cudf.core.Series` target

    Args:
        data: Table with features.
        features: features names.
        roles: Roles specifier.
        task: Task specifier.
        index_ok: if input data index is reset before use (if not, the class will to a reset_index)
        **kwargs: Series, array like attrs target, group etc...

    """

    _dataset_type = "DaskCudfDataset"

    def __init__(
        self,
        data: Optional[DataFrame_dask] = None,
        roles: Optional[RolesDict] = None,
        task: Optional[Task] = None,
        index_ok: bool = False,
        **kwargs: Series_dask
    ):
        if roles is None:
            roles = {}
        # parse parameters
        # check if target, group etc .. defined in roles
        for f in roles:
            for k, r in zip(valid_array_attributes, array_attr_roles):
                if roles[f].name == r:
                    kwargs[k] = data[f]
                    roles[f] = DropRole()
        if not index_ok:
            size = len(data.index)
            data["index"] = data.index
            mapping = dict(zip(data.index.compute().values_host, np.arange(size)))
            data["index"] = data["index"].map(mapping).persist()
            data = data.set_index("index", drop=True, sorted=True)
            if 'index' in data.columns.to_list():
                data = data.drop(['index'], axis=1)
            data = data.persist()
            for val in kwargs:
                col_name = kwargs[val].name if isinstance(kwargs[val], dask_cudf.Series) else list(kwargs[val].columns)
                kwargs[val] = kwargs[val].reset_index(drop=False)
                kwargs[val]["index"] = kwargs[val]["index"].map(mapping).persist()
                kwargs[val] = kwargs[val].set_index("index", drop=True, sorted=True)[
                    col_name
                ]

        self._initialize(task, **kwargs)
        if data is not None:
            self.set_data(data, data.columns, roles)

    def _check_dtype(self):
        """Check if dtype in .set_data is ok and cast if not."""
        date_columns = []
        self.dtypes = {}
        for f in self.roles:
            if self.roles[f].name == "Datetime":
                date_columns.append(f)
            else:
                self.dtypes[f] = self.roles[f].dtype

        try:
            self.data = self.data.astype(self.dtypes).persist()
        except:
            pass
        # handle dates types

        self.data = self.data.map_partitions(
            self._convert_datetime, date_columns, meta=self.data
        ).persist()

        for i in date_columns:
            self.dtypes[i] = np.datetime64

    @staticmethod
    def slice_cudf(data, rows, cols):
        mini = data.index[0]
        maxi = data.index[-1]
        step = data.index[1] - data.index[0]
        new_rows = [x for x in rows if x >= mini and x <= maxi]
        inds = [int((x - mini) / step) for x in new_rows]
        return data.iloc[inds, cols]

    def _get_slice(self, data: dask_cudf.DataFrame, k: Tuple[RowSlice, ColSlice]) -> dask_cudf.DataFrame:
        """Get 2d slice.

        Args:
            data: Data.
            k: Tuple of integer sequences.

        Returns:
            2d slice.

        """
        rows, cols = k

        if isinstance(data, dask_cudf.DataFrame):

            return data.loc[rows][data.columns[cols]]

        else:
            raise TypeError("wrong data type for _get_slice() in " + self.__class__.__name__)

    def _get_2d(self, data: DataFrame, k: Tuple[IntIdx, IntIdx]) -> FrameOrSeries:
        """Define 2d slice of table.

        Args:
            data: Table with data.
            k: Sequence of `int` indexes or `int`.

        Returns:
            2d sliced table.

        """

        rows, cols = k
        if cols.size == 0:
            if isinstance(rows, slice):
                return data[cols]
            return self._get_slice(data, k)
        if isinstance(rows, np.ndarray) and\
           isinstance(cols, np.ndarray):
            return self._get_slice(data, k)
        return data.iloc[rows, cols]

    @staticmethod
    def _get_rows(data: DataFrame_dask, k) -> FrameOrSeries_dask:
        """Define how to get rows slice.

        Args:
            data: Table with data.
            k: Sequence of `int` indexes or `int`.

        Returns:
            Sliced rows.

        """
        if isinstance(k, cp.ndarray):
            k = cp.asnumpy(k)
        if isinstance(k, slice):
            return data.persist()
        return data.loc[k].persist()

    def to_cudf(self) -> CudfDataset:
        """Convert to class:`CudfDataset`.

        Returns:
            Same dataset in class:`CudfDataset` format.
        """
        data = None
        if self.data is not None:
            data = self.data.compute()
        roles = self.roles
        task = self.task

        params = dict(((x, self.__dict__[x].compute()) for x in self._array_like_attrs))
        return CudfDataset(data, roles, task, **params)

    def to_numpy(self) -> "NumpyDataset":
        """Convert to class:`NumpyDataset`.

        Returns:
            Same dataset in class:`NumpyDataset` format.

        """

        return self.to_cudf().to_numpy()

    def to_cupy(self) -> "CupyDataset":
        """Convert dataset to cupy.

        Returns:
            Same dataset in CupyDataset format

        """

        return self.to_cudf().to_cupy()

    def to_sparse_gpu(self) -> "CupySparseDataset":

        return self.to_cupy().to_sparse_gpu()

    def to_pandas(self) -> "PandasDataset":
        """Convert dataset to pandas.

        Returns:
            Same dataset in PandasDataset format

        """

        return self.to_cudf().to_pandas()

    def to_daskcudf(
        self, npartitions: int = 1, index_ok: bool = True
    ) -> "DaskCudfDataset":
        """Empty method to return self

        Returns:
            self
        """

        return self

    @staticmethod
    def _hstack(datasets: Sequence[DataFrame_dask]) -> DataFrame_dask:
        """Define how to concat features arrays.

        Args:
            datasets: Sequence of tables.

        Returns:
            concatenated table.

        """
        cols = []
        res_datasets = []
        for i, data in enumerate(datasets):
            if data is not None:
                cols.extend(data.columns)
                res_datasets.append(data)

        res = dask_cudf.concat(res_datasets, axis=1)
        mapper = dict(zip(np.arange(len(cols)), cols))
        res = res.rename(columns=mapper)
        return res

    @staticmethod
    def from_dataset(
        dataset: "DaskCudfDataset", npartitions: int = 1, index_ok: bool = True
    ) -> "DaskCudfDataset":
        """Convert DaskCudfDataset to DaskCudfDataset
        (for now, later we add  , npartitionsto_daskcudf() to other classes
        using from_pandas and from_cudf.

        Returns:
            Converted to pandas dataset.

        """
        return dataset.to_daskcudf(npartitions, index_ok=index_ok)

    @property
    def shape(self) -> Tuple[Optional[int], Optional[int]]:
        """Get size of 2d feature matrix.

        Returns:
            Tuple of 2 elements.

        """
        rows, cols = self.data.shape[0].compute(), len(self.features)
        return rows, cols


class SeqDaskCudfDataset(DaskCudfDataset):
    """Sequential Dataset, that contains info in dask_cudf.DataFrame format.
    """

    _dataset_type = "SeqDaskCudfDatset"

    def _initialize(self, task: Optional[Task], **kwargs: Any):
        """Initialize empty dataset with task and array like attributes.

        Args:
            task: Task name for dataset.
            **kwargs: 1d arrays like attrs like target, group etc.

        """
        super()._initialize(task, **kwargs)
        self._idx = None

    @property
    def idx(self) -> Any:
        """Get idx attribute.

        Returns:
            Any, array like or ``None``.

        """
        return self._idx

    @idx.setter
    def idx(self, val: Any):
        """Set idx array or ``None``.

        Args:
            val: Some idx or ``None``.

        """
        self._idx = val

    def __init__(
        self,
        data: Optional[DenseSparseArray],
        features: NpFeatures = (),
        roles: NpRoles = None,
        idx: List = (),
        task: Optional[Task] = None,
        name: Optional[str] = "seq",
        scheme: Optional[dict] = None,
        **kwargs: Series
    ):
        self.name = name
        if scheme is not None:
            self.scheme = scheme
        else:
            self.scheme = {}

        super().__init__(data, roles, task, **kwargs)
        if data is not None:
            self.set_idx(data, idx)

    def set_idx(self, data: DenseSparseArray, idx: Optional[List] = None):
        """Inplace set data, features, roles for empty dataset.

        Args:
            data: 2d array like or ``None``.
            roles: roles dict.
            idx: list.

        """
        if idx is None:
            idx = np.arange(len(data)).reshape(-1, 1)
        self.idx = idx
        self._check_dtype()

    def __len__(self):
        return len(self.idx)

    def _get_cols_idx(self, columns: Union[Sequence[str], str]) -> Union[Sequence[int], int]:
        """Get numeric index of columns by column names.

        Args:
            columns: sequence of columns of single column.

        Returns:
            sequence of int indexes or single int.

        """
        if type(columns) is str:
            idx = self.data.columns.get_loc(columns)

        else:
            idx = self.data.columns.get_indexer(columns)

        return idx

    def __getitem__(self, k: Tuple[RowSlice, ColSlice]) -> Union["LAMLDataset", LAMLColumn]:
        """Select a subset of dataset.

        Define how to slice a dataset
        in way ``dataset[[1, 2, 3...], ['feat_0', 'feat_1'...]]``.
        Default behavior based on ``._get_cols``, ``._get_rows``, ``._get_2d``.

        Args:
            k: First element optional integer columns indexes,
                second - optional feature name or list of features names.

        Returns:
            Subset.

        """
        if type(k) is tuple:
            rows, cols = k
            if isinstance(cols, str):
                cols = [cols]
        else:
            rows = k
            cols = None

        is_slice = False
        if isinstance(rows, slice):
            is_slice = True

        rows = [rows] if isinstance(rows, int) else np.arange(self.__len__()) if isinstance(rows, slice) else rows
        temp_idx = self.idx[rows]
        rows = []
        idx_new = []
        _c = 0
        for i in temp_idx:
            rows.extend(list(i))
            idx_new.append(list(np.arange(len(i)) + _c))
            _c += len(i)
        idx_new = np.array(idx_new, dtype=object)

        rows = np.array(sorted(list(set(rows))))

        if is_slice:
            idx_new = self.idx
            rows = np.arange(len(self.data))
        else:
            warnings.warn(
                "Resulted sequential dataset may have different structure. It's not recommended to slice new dataset"
            )

        # case when columns are defined
        if cols is not None:
            idx = self._get_cols_idx(cols)
            data = self._get_2d(self.data, (rows, idx))

            # case of multiple columns - return LAMLDataset
            roles = dict(((x, self.roles[x]) for x in self.roles if x in cols))
        else:
            roles = self.roles
            data = self._get_rows(self.data, rows)

        # case when rows are defined
        if rows is None:
            dataset = self.empty()
        else:
            dataset = copy(self)
            params = dict(((x, self._get_rows(self.__dict__[x], rows)) for x in self._array_like_attrs))
            dataset._initialize(self.task, **params)

        dataset.set_data(data, None, roles)
        dataset.set_idx(data, idx_new)

        return dataset

    def get_first_frame(self, k: Tuple[RowSlice, ColSlice] = None) -> DaskCudfDataset:
        """Select a subset of dataset with only first elements of the sequential features.

        Define how to slice a dataset in way ``dataset[[1, 2, 3...], ['feat_0', 'feat_1'...]]``.
        Default behavior based on ``._get_cols_idx``, ``._get_slice``.

        Args:
            k: First element optional integer columns indexes,
                second - optional feature name or list of features names.

        Returns:
            respective Dataset with first elements of the sequential features.

        """
        self._check_dtype()
        if k is None:
            k = slice(None, None, None)

        if type(k) is tuple:
            rows, cols = k
            if isinstance(cols, str):
                cols = [cols]
        else:
            rows = k
            cols = None

        rows = [rows] if isinstance(rows, int) else np.arange(self.__len__()) if isinstance(rows, slice) else rows

        # case when columns are defined
        if cols is not None:
            idx = self._get_cols_idx(cols)
            roles = dict(((x, self.roles[x]) for x in self.roles if x in cols))
        else:
            roles = self.roles
            idx = self._get_cols_idx(self.data.columns)

        data = self.data
        if type(self.idx == np.ndarray) and (np.isnan(self.idx).any()):
            size = len(data)
            data = dask_cudf.concat([data, dask_cudf.from_cudf(cudf.DataFrame([cp.nan], index=[size]), npartitions=self.data.npartitions)])
            data = data[data.columns[:-1]]
            self.idx = np.nan_to_num(self.idx, nan=[-1])
        first_frame_idx = [self.idx[i][0] for i in rows]

        data = self._get_slice(data, (first_frame_idx, idx))
        if rows is None:
            dataset = DaskCudfDataset(None, deepcopy(roles), task=self.task)
        else:
            dataset = DaskCudfDataset(data, deepcopy(roles), task=self.task)
        return dataset

    def apply_func(self, k: Tuple[RowSlice, ColSlice] = None, func: Callable = None) -> dask_cudf.DataFrame:
        """Apply function to each sequence.

        Args:
            k: First element optional integer columns indexes,
                second - optional feature name or list of features names.
            func: any callable function

        Returns:
            output dask_cudf.DataFrame

        """
        self._check_dtype()
        if k is None:
            k = slice(None, None, None)

        if type(k) is tuple:
            rows, cols = k
            if isinstance(cols, str):
                cols = [cols]
        else:
            rows = k
            cols = None

        rows = [rows] if isinstance(rows, int) else np.arange(self.__len__()) if isinstance(rows, slice) else rows

        # case when columns are defined
        if cols is not None:

            data = []
            _d = self.data[cols].values
            for row in rows:
                data.append(func(_d[self.idx[row]].compute()))
        else:
            data = []
            _d = self.data.values
            for row in rows:
                data.append(func(_d[self.idx[row]].compute()))

        return dask_cudf.from_cudf(cudf.DataFrame(data), npartitions=self.data.npartitions)

    def to_daskcudf(self) -> DaskCudfDataset:
        """Convert to plain DaskCudfDataset.

        Returns:
            Same dataset in DaskCudfDataset format without sequential features.

        """
        # check for empty case
        data = None if self.data is None else dask_cudf.DataFrame(self.data, columns=self.features)
        roles = self.roles
        # target and etc ..
        params = dict(((x, dask_cudf.Series(self.__dict__[x])) for x in self._array_like_attrs))
        task = self.task

        return DaskCudfDataset(data, roles, task, **params)

    @classmethod
    def concat(cls, datasets: Sequence["LAMLDataset"]) -> "LAMLDataset":
        """Concat multiple dataset.

        Default behavior - takes empty dataset from datasets[0]
        and concat all features from others.

        Args:
            datasets: Sequence of datasets.

        Returns:
            Concated dataset.

        """
        for check in cls._concat_checks:
            check(datasets)

        idx = datasets[0].idx
        dataset = datasets[0].empty()
        data = []
        features = []
        roles = {}

        atrs = set(dataset._array_like_attrs)
        for ds in datasets:
            data.append(ds.data)
            features.extend(ds.features)
            roles = {**roles, **ds.roles}
            for atr in ds._array_like_attrs:
                if atr not in atrs:
                    dataset._array_like_attrs.append(atr)
                    dataset.__dict__[atr] = ds.__dict__[atr]
                    atrs.update({atr})

        data = cls._hstack(data)
        dataset.set_data(data, None, roles)
        dataset.set_idx(data, idx)

        return dataset
