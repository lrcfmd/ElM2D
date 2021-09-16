# -*- coding: utf-8 -*-
"""
Created on Wed Sep  8 01:36:51 2021

@author: sterg
"""

from scipy.stats import wasserstein_distance as scipy_wasserstein_distance
from scipy.spatial.distance import cdist

# generate test data
np.random.seed(42)
rows = 200
cols = 100
[U, V, U_weights, V_weights] = [np.random.rand(rows, cols) for i in range(4)]

testpairs = np.array([(1, 2), (2, 3), (3, 4)])

# %% unit tests
one_set_check = scipy_wasserstein_distance(
    U[0], U[1], u_weights=U_weights[0], v_weights=U_weights[1]
)
two_set_check = scipy_wasserstein_distance(
    U[0], V[0], u_weights=U_weights[0], v_weights=V_weights[0]
)
i, j = pairs[0]
one_sparse_check = scipy_wasserstein_distance(
    U[i], U[j], u_weights=U_weights[i], v_weights=U_weights[j]
)
two_sparse_check = scipy_wasserstein_distance(
    U[i], V[j], u_weights=U_weights[i], v_weights=V_weights[j]
)

tol = 1e-6
print(
    abs(one_set[0, 1] - one_set_check) < tol,
    abs(two_set[0, 0] - two_set_check) < tol,
    abs(one_set_sparse[0, 0] - one_sparse_check) < tol,
    abs(two_set_sparse[0, 0] - two_sparse_check) < tol,
)


# %% wasserstein helper functions
def my_wasserstein_distance(u_uw, v_vw):
    """
    Return Earth Mover's distance using concatenated values and weights.

    Parameters
    ----------
    u_uw : 1D numeric array
        Horizontally stacked values and weights of first distribution.
    v_vw : TYPE
        Horizontally stacked values and weights of second distribution.

    Returns
    -------
    distance : numeric scalar
        Earth Mover's distance given two distributions.

    """
    # split into values and weights
    n = len(u_uw)
    i = n // 2
    u = u_uw[0:i]
    uw = u_uw[i:n]
    v = v_vw[0:i]
    vw = v_vw[i:n]
    # calculate distance
    distance = wasserstein_distance(u, v, u_weights=uw, v_weights=vw)
    return distance


def join_wasserstein(U, V, Uw, Vw):
    """
    Horizontally stack values and weights for each distribution.

    Weights are added as additional columns to values.

    Example:
        u_uw, v_vw = join_wasserstein(u, v, uw, vw)
        d = my_wasserstein_distance(u_uw, v_vw)
        cdist(u_uw, v_vw, metric=my_wasserstein_distance)

    Parameters
    ----------
    u : 1D or 2D numeric array
        First set of distribution values.
    v : 1D or 2D numeric array
        Second set of values of distribution values.
    uw : 1D or 2D numeric array
        Weights for first distribution.
    vw : 1D or 2D numeric array
        Weights for second distribution.

    Returns
    -------
    u_uw : 1D or 2D numeric array
        Horizontally stacked values and weights of first distribution.
    v_vw : TYPE
        Horizontally stacked values and weights of second distribution.

    """
    U_Uw = np.concatenate((U, Uw), axis=1)
    V_Vw = np.concatenate((V, Vw), axis=1)
    return U_Uw, V_Vw


@njit(fastmath=fastmath, debug=debug)
def wasserstein_distance_check(u_values, v_values, u_weights=None, v_weights=None, p=1):
    r"""
    Compute first

    Compute, between two one-dimensional distributions :math:`u` and
    :math:`v`, whose respective CDFs are :math:`U` and :math:`V`, the
    statistical distance that is defined as:
    .. math::
        l_p(u, v) = \left( \int_{-\infty}^{+\infty} |U-V|^p \right)^{1/p}
    p is a positive parameter; p = 1 gives the Wasserstein distance, p = 2
    gives the energy distance.

    Parameters
    ----------
    u_values, v_values : array_like
        Values observed in the (empirical) distribution.
    u_weights, v_weights : array_like, optional
        Weight for each value. If unspecified, each value is assigned the same
        weight.
        `u_weights` (resp. `v_weights`) must have the same length as
        `u_values` (resp. `v_values`). If the weight sum differs from 1, it
        must still be positive and finite so that the weights can be normalized
        to sum to 1.

    Returns
    -------
    distance : float
        The computed distance between the distributions.

    Notes
    -----
    The input distributions can be empirical, therefore coming from samples
    whose values are effectively inputs of the function, or they can be seen as
    generalized functions, in which case they are weighted sums of Dirac delta
    functions located at the specified values.

    References
    ----------
    .. [1] Bellemare, Danihelka, Dabney, Mohamed, Lakshminarayanan, Hoyer,
           Munos "The Cramer Distance as a Solution to Biased Wasserstein
           Gradients" (2017). :arXiv:`1705.10743`.
    """
    u_sorter = np.argsort(u_values)
    v_sorter = np.argsort(v_values)

    all_values = np.concatenate((u_values, v_values))
    all_values.sort(kind="mergesort")

    # Compute the differences between pairs of successive values of u and v.
    deltas = np.diff(all_values)

    # Get the respective positions of the values of u and v among the values of
    # both distributions.
    u_cdf_indices = u_values[u_sorter].searchsorted(all_values[:-1], "right")
    v_cdf_indices = v_values[v_sorter].searchsorted(all_values[:-1], "right")

    # Calculate the CDFs of u and v using their weights, if specified.
    if u_weights is None:
        u_cdf = u_cdf_indices / u_values.size
    else:
        u_sorted_cumweights = np.concatenate(([0], np.cumsum(u_weights[u_sorter])))
        u_cdf = u_sorted_cumweights[u_cdf_indices] / u_sorted_cumweights[-1]

    if v_weights is None:
        v_cdf = v_cdf_indices / v_values.size
    else:
        v_sorted_cumweights = np.concatenate(([0], np.cumsum(v_weights[v_sorter])))
        v_cdf = v_sorted_cumweights[v_cdf_indices] / v_sorted_cumweights[-1]

    # Compute the value of the integral based on the CDFs.
    # If p = 1 or p = 2, we avoid using np.power, which introduces an overhead
    # of about 15%.
    if p == 1:
        return np.sum(np.multiply(np.abs(u_cdf - v_cdf), deltas))
    if p == 2:
        return np.sqrt(np.sum(np.multiply(np.square(u_cdf - v_cdf), deltas)))
    return np.power(
        np.sum(np.multiply(np.power(np.abs(u_cdf - v_cdf), p), deltas)), 1 / p
    )


U_Uw, V_Vw = join_wasserstein(U, V, U_weights, V_weights)


# %% CODE GRAVEYARD
# def setdiff(a, b):
#     """
#     Find the rows in a which are not in b.

#     Source: modified from https://stackoverflow.com/a/11903368/13697228
#     See also: https://www.mathworks.com/help/matlab/ref/double.setdiff.html

#     Parameters
#     ----------
#     a : 2D array
#         Set of vectors.
#     b : 2D array
#         Set of vectors.

#     Returns
#     -------
#     out : 2D array
#         Set of vectors in a that are not in b.

#     """
#     a_rows = a.view([("", a.dtype)] * a.shape[1])
#     b_rows = b.view([("", b.dtype)] * b.shape[1])
#     out = np.setdiff1d(a_rows, b_rows).view(a.dtype).reshape(-1, a.shape[1])
#     return out

# n_neighbors = 3
# n_neighbors2 = 1