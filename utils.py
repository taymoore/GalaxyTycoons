import pandas as pd
from typing import Tuple, Union


def align_and_interpolate(
    *args: Union[pd.Series, pd.DataFrame],
) -> Tuple[Union[pd.Series, pd.DataFrame], ...]:
    """
    Does not work with bool!
    """

    def __align_and_interpolate(
        a: Union[pd.Series, pd.DataFrame], b: Union[pd.Series, pd.DataFrame]
    ) -> Tuple[Union[pd.Series, pd.DataFrame], ...]:
        a, b = a.align(b, axis=0)
        return (
            a.interpolate(method="index", limit_area="inside"),
            b.interpolate(method="index", limit_area="inside"),
        )

    arg_list = [arg.copy() for arg in args]
    for a_index, a_series in enumerate(arg_list):
        for b_index, b_series in enumerate(arg_list[a_index + 1 :]):
            (
                arg_list[a_index],
                arg_list[b_index + a_index + 1],
            ) = __align_and_interpolate(a_series, b_series)
    return tuple(arg_list)


def align_add(
    a: Union[pd.Series, pd.DataFrame],
    b: Union[pd.Series, pd.DataFrame],
    by_column_index: bool = False,
) -> Union[pd.Series, pd.DataFrame]:
    """
    Return a + b filling in missing indexes by interpolation
    by_column_index allows subtracting DataFrame columns by column position instead of name
    """
    if by_column_index:
        b = b.copy()
        b.columns = a.columns
    aligned_data = [
        data.interpolate(method="index", limit_area="inside")
        for data in a.align(b, axis=0)
    ]
    return aligned_data[0].add(aligned_data[1], axis="index")
