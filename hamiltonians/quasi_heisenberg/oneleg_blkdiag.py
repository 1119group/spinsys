import spinsys as s
from spinsys.utils.cache import Globals as G
import numpy as np
import scipy.sparse as ss
import functools


def diagonals(N, h, c, phi, Jz, curr_j, mode):
    """Generates the diagonal elements of the hamiltonian.

    Args: "N" number of sites
          "h" disorder strength
          "c" trancendental number
          "phi" phase
          "Jz" z component coupling constant between sites
          "curr_j" total <Sz> for the current block
          "mode" "open" or "periodic" boundary conditions
    Returns: csc_matrix
    """
    # cache to ram since the interaction contribution stays the same
    #  across configurations
    @functools.lru_cache(maxsize=None)
    def interaction(N, curr_j, mode):
        diagonal = np.zeros(mat_dim)
        for i, basis in enumerate(basis_set):
            basis = [conv[s] for s in basis]
            # Interaction contributions. The conversion of 0's to -1's is
            #  warranted by the use of multiplication here.
            #  A basis state of [1, 1, -1, -1] in <Sz_i Sz_(i+1)> would
            #  yield 1/4 - 1/4 + 1/4 - 1/4 = 0. We can achieve the same
            #  effect here if we take 1/4 * [1, 1, -1, -1] * [-1, 1, 1, -1],
            #  the last two terms being the current state multiplied by
            #  its shifted self element wise. The results are then summed.
            if mode == 'periodic':
                inter_contrib = sum(map(lambda x, y: x * y, basis,
                                        [basis[-1]] + basis[:-1]))
            elif mode == 'open':
                inter_contrib = sum(map(lambda x, y: x * y, basis[1:],
                                        basis[:-1]))
            diagonal[i] = Jz * 0.25 * inter_contrib
        return diagonal

    sites = np.array(range(1, N + 1))
    disorder = h * np.cos(2 * np.pi * c * sites + phi)
    basis_set = s.half.generate_complete_basis(N, curr_j)[0]
    conv = {1: 1, 0: -1}         # dict for conversion of 0's to -1's
    mat_dim = len(basis_set)     # size of the block for the given total <Sz>
    diagonal = np.empty(mat_dim)
    for i, basis in enumerate(basis_set):
        # convert 0's to -1's so the basis configuration would look like
        #  [-1, -1, 1, 1] instead of [0, 0, 1, 1]
        basis = [conv[s] for s in basis]
        # Disorder contributions.
        disord_contrib = sum(disorder * basis)
        diagonal[i] = 0.5 * disord_contrib
    diagonal += interaction(N, curr_j, mode)
    ind = np.arange(0, mat_dim)
    return ss.csc_matrix((diagonal, (ind, ind)), shape=[mat_dim, mat_dim])


@functools.lru_cache(maxsize=None)
@s.utils.cache.matcache
def off_diagonals(N, Jxy, curr_j, mode):
    """Generates the off-diagonal elements of the hamiltonian.

    Args: "N" number of sites
          "Jxy" x/y component coupling constant between adjacent sites
          "curr_j" total <Sz> for the current block
          "mode" "open" or "periodic" boundary conditions
    Returns: csc_matrix
    """
    basis_set = s.half.generate_complete_basis(N, curr_j)[0]
    lb = 0 if mode == 'periodic' else 1
    # pairs of adjacent elements to switch in a given basis for
    #  testing. This corresponds to nearest neighbor interaction
    adj_pairs = [(i - 1, i) for i in range(lb, N)]
    mat_dim = len(basis_set)    # size of the block for the given total <Sz>
    hilbert_dim = 2 ** N        # size of the Hilbert space
    col_ind, row_ind, data = [], [], []
    # The "bra" in <phi|S_+S_- + S_-S_+|phi'>
    for i, bi in enumerate(basis_set):
        for pair in adj_pairs:
            bj = bi[:]          # the "ket" in <phi|S_+S_- + S_-S_+|phi'>
            bj[pair[0]], bj[pair[1]] = bj[pair[1]], bj[pair[0]]
            # Test to see if the pair of bases are indeed only different
            #  at two sites i.e. [1, 1, 0, 0] => [1, 0, 1, 0].
            #  If the premise is true, subtracting one basis from another
            #  element wise with each taken the absolute value would yield
            #  2.
            if sum(map(lambda x, y: abs(x - y), bi, bj)) == 2:
                j_loc = hilbert_dim - s.utils.misc.bin_to_dec(bj) - 1
                j = G['complete_basis'][N][curr_j][1][j_loc]
                col_ind.append(j)
                row_ind.append(i)
                data.append(0.5 * Jxy)
    return ss.csc_matrix((data, (row_ind, col_ind)), shape=[mat_dim, mat_dim])


def H(N, h, c, phi, Jxy=1, Jz=1, curr_j=0, mode='open'):
    """Generates the full hamiltonian for a block corresponding to a
    specific total spin.

    Args: "N" number of sites
          "h" disorder strength
          "c" trancendental number
          "phi" phase
          "J" coupling constant between sites
          "curr_j" total <Sz> for the current block
          "mode" "open" or "periodic" boundary conditions
    Returns: csc_matrix
    """
    return diagonals(N, h, c, phi, Jz, curr_j, mode) + \
           off_diagonals(N, Jxy, curr_j, mode)
