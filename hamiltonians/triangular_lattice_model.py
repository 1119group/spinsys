import copy
import functools
import numpy as np
from scipy import sparse
from spinsys import constructors, half, dmrg, exceptions, utils


class SiteVector(constructors.PeriodicBCSiteVector):

    def __init__(self, ordered_pair, Nx, Ny):
        super().__init__(ordered_pair, Nx, Ny)

    def angle_with(self, some_site):
        """Returns the angle * 2 between (some_site - self) with the
        horizontal. Only works on nearest neighbors
        """
        Δx, Δy = some_site - self
        if Δx == 0:
            if Δy != 0:
                return -2 * np.pi / 3
        elif Δy == 0:
            if Δx != 0:
                return 0
        else:
            return 2 * np.pi / 3

    def a1_hop(self, stride):
        vec = self.xhop(stride)
        if vec == self:
            raise exceptions.SameSite
        return vec

    def a2_hop(self, stride):
        vec = self.xhop(-1 * stride).yhop(stride)
        if vec == self:
            raise exceptions.SameSite
        return vec

    def a3_hop(self, stride):
        vec = self.yhop(-stride)
        if vec == self:
            raise exceptions.SameSite
        return vec

    def b1_hop(self, stride):
        """hop in the a1 - a3 aka b1 direction. Useful for second nearest
        neighbor coupling interactions
        """
        vec = self.xhop(stride).yhop(stride)
        if vec == self:
            raise exceptions.SameSite
        return vec

    def b2_hop(self, stride):
        vec = self.xhop(-2 * stride).yhop(stride)
        if vec == self:
            raise exceptions.SameSite
        return vec

    def b3_hop(self, stride):
        vec = self.b1_hop(-stride).b2_hop(-stride)
        if vec == self:
            raise exceptions.SameSite
        return vec

    def _neighboring_sites(self, strides, funcs):
        neighbors = []
        for stride in strides:
            for func in funcs:
                try:
                    neighbors.append(func(stride))
                except exceptions.SameSite:
                    continue
        return neighbors

    @property
    def nearest_neighboring_sites(self, all=False):
        strides = [1, -1] if all else [1]
        funcs = [self.a1_hop, self.a2_hop, self.a3_hop]
        return self._neighboring_sites(strides, funcs)

    @property
    def second_neighboring_sites(self, all=False):
        """with the all option enabled the method will enumerate all
        the sites that are second neighbors to the current site.
        Otherwise it will only enumerate the sites along the b1, b2
        and b3 directions
        """
        strides = [1, -1] if all else [1]
        funcs = [self.b1_hop, self.b2_hop, self.b3_hop]
        return self._neighboring_sites(strides, funcs)

    @property
    def third_neighboring_sites(self, all=False):
        strides = [2, -2] if all else [2]
        funcs = [self.a1_hop, self.a2_hop, self.a3_hop]
        return self._neighboring_sites(strides, funcs)


class SemiPeriodicBCSiteVector(SiteVector):

    """A version of SiteVector that is periodic only along the x
    direction
    """

    def __init__(self, ordered_pair, Nx, Ny):
        super().__init__(ordered_pair, Nx, Ny)

    def diff(self, other):
        """Finds the shortest distance from this site to the other"""
        Δx = self.x - other.x
        Δy = self.y - other.y
        return (Δx, Δy)

    def yhop(self, stride):
        new_vec = copy.copy(self)
        new_y = self.y + stride
        if new_y // self.Ny == self.x // self.Ny:
            new_vec.y = new_y
        else:
            raise exceptions.OutOfBoundsError("Hopping off the lattice")
        return new_vec

    @property
    def neighboring_sites(self):
        neighbors = []
        funcs = [self.xhop, self.yhop]
        for Δ in [1, -1]:
            for func in funcs:
                try:
                    neighbors.append(func(Δ).lattice_index)
                except exceptions.OutOfBoundsError:
                    continue
            try:
                neighbors.append(self.xhop(Δ).yhop(-Δ).lattice_index)
            except exceptions.OutOfBoundsError:
                continue
        return neighbors


@functools.lru_cache(maxsize=None)
def _generate_bonds(Nx, Ny):
    N = Nx * Ny
    vec = SiteVector((0, 0), Nx, Ny)
    range_orders = [set(), set(), set()]  # sets de-duplicates the list of bonds
    for i in range(N):
        nearest_neighbor = vec.nearest_neighboring_sites
        second_neighbor = vec.second_neighboring_sites
        third_neighbor = vec.third_neighboring_sites
        neighbors = [nearest_neighbor, second_neighbor, third_neighbor]
        for leap, bonds in enumerate(range_orders):
            for n in neighbors[leap]:
                # sort them so identical bonds will always have the same hash
                bond = sorted((vec, n))
                bonds.add(tuple(bond))
        vec = vec.next_site()
    return range_orders


@functools.lru_cache(maxsize=None)
def _gen_full_ops(N):
    σ_p = constructors.raising()
    σ_m = constructors.lowering()
    σz = constructors.sigmaz()
    p_mats = [half.full_matrix(σ_p, k, N) for k in range(N)]
    m_mats = [half.full_matrix(σ_m, k, N) for k in range(N)]
    z_mats = [half.full_matrix(σz, k, N) for k in range(N)]
    return p_mats, m_mats, z_mats


def _gen_z_pm_ops(N, bonds):
    """generate the H_z and H_pm components of the Hamiltonian"""
    H_pm = H_z = 0
    p_mats, m_mats, z_mats = _gen_full_ops(N)
    for bond in bonds:
        site1, site2 = bond
        i, j = site1.lattice_index, site2.lattice_index
        H_pm += p_mats[i].dot(m_mats[j]) + m_mats[i].dot(p_mats[j])
        H_z += z_mats[i].dot(z_mats[j])
    return H_pm, H_z


@functools.lru_cache(maxsize=None)
def hamiltonian_dp_components(Nx, Ny):
    """Generate the reusable pieces of the hamiltonian"""
    N = Nx * Ny
    nearest, second, third = _generate_bonds(Nx, Ny)
    H_pm1, H_z1 = _gen_z_pm_ops(N, nearest)
    H_pm2, H_z2 = _gen_z_pm_ops(N, second)
    H_pm3, H_z3 = _gen_z_pm_ops(N, third)

    H_ppmm = H_pmz = 0
    p_mats, m_mats, z_mats = _gen_full_ops(N)
    for bond in nearest:
        site1, site2 = bond
        i, j = site1.lattice_index, site2.lattice_index
        γ = np.exp(1j * site1.angle_with(site2))

        H_ppmm += \
            γ * p_mats[i].dot(p_mats[j]) + \
            γ.conj() * m_mats[i].dot(m_mats[j])

        H_pmz += 1j * (γ.conj() * z_mats[i].dot(p_mats[j]) -
                       γ * z_mats[i].dot(m_mats[j]) +
                       γ.conj() * p_mats[i].dot(z_mats[j]) -
                       γ * m_mats[i].dot(z_mats[j]))

    return H_pm1, H_z1, H_ppmm, H_pmz, H_pm2, H_z2, H_z3, H_pm3


def hamiltonian_dp(Nx, Ny, J_pm=0, J_z=0, J_ppmm=0, J_pmz=0, J2=0, J3=0):
    """Generates hamiltonian for the triangular lattice model with
    direct product

    Parameters
    --------------------
    Nx: int
        number of sites along the x-direction
    Ny: int
        number of sites along the y-direction
    J_pm: float
        J_+- parameter
    J_z: float
        J_z parameter
    J_ppmm: float
        J_++-- parameter
    J_pmz: float
        J_+-z parameter
    J2: float
        second nearest neighbor interaction parameter
    J3: float
        third nearest neighbor interaction parameter

    Returns
    --------------------
    H: scipy.sparse.csc_matrix
    """
    components = hamiltonian_dp_components(Nx, Ny)
    H_pm1, H_z1, H_ppmm, H_pmz, H_pm2, H_z2, H_z3, H_pm3 = components
    nearest_neighbor_terms = J_pm * H_pm1 + J_z * H_z1 + J_ppmm * H_ppmm + J_pmz * H_pmz
    second_neighbor_terms = third_neighbor_terms = 0
    if not J2 == 0:
        second_neighbor_terms = J2 * (H_pm2 + J_z / J_pm * H_z2)
    if not J3 == 0:
        third_neighbor_terms = J3 * (H_pm3 + J_z / J_pm * H_z3)
    return nearest_neighbor_terms + second_neighbor_terms + third_neighbor_terms


class DMRG_Hamiltonian(dmrg.Hamiltonian):

    def __init__(self, Nx, Ny, J_pm=0, J_z=0, J_ppmm=0, J_pmz=0):
        self.generators = {
            '+': constructors.raising(),
            '-': constructors.lowering(),
            'z': constructors.sigmaz()
        }
        self.N = Nx * Ny
        self.Nx = Nx
        self.Ny = Ny
        self.J_pm = J_pm
        self.J_z = J_z
        self.J_ppmm = J_ppmm
        self.J_pmz = J_pmz
        super().__init__()

    def initialize_storage(self):
        init_block = sparse.csc_matrix(([], ([], [])), dims=[2, 2])
        init_ops = self.generators
        self.storage = dmrg.Storage(init_block, init_block, init_ops)

    def newsite_ops(self, size):
        return dict((i, sparse.kron(sparse.eye(size // 2), self.generators[i]))
                    for i in self.generators.keys())

    # TODO: Inconsistent shapes error at runtime
    def block_newsite_interaction(self, block_key):
        block_side, curr_site = block_key
        site = SemiPeriodicBCSiteVector.from_index(curr_site, self.Nx, self.Ny)
        neighbors = [i for i in site.neighboring_sites if i < curr_site]

        H_pm_new = H_z_new = H_ppmm_new = H_pmz_new = 0
        for i in neighbors:
            key = (block_side, i + 1)
            block_ops = self.storage.get_item(key).ops
            site_ops = self.generators

            H_pm_new += \
                sparse.kron(block_ops['+'], site_ops['-']) + \
                sparse.kron(block_ops['-'], site_ops['+'])

            H_z_new += sparse.kron(block_ops['z'], site_ops['z'])

            H_ppmm_new += \
                sparse.kron(block_ops['+'], site_ops['+']) + \
                sparse.kron(block_ops['-'], site_ops['-'])

            H_pmz_new += \
                sparse.kron(block_ops['z'], site_ops['+']) + \
                sparse.kron(block_ops['z'], site_ops['-']) + \
                sparse.kron(block_ops['+'], site_ops['z']) + \
                sparse.kron(block_ops['-'], site_ops['z'])

        return self.J_pm * H_pm_new + self.J_z * H_z_new + \
            self.J_ppmm * H_ppmm_new + self.J_pmz * H_pmz_new


class BlochFunc:

    def __init__(self, lead, decs, norm=None):
        self.lead = lead
        self.decs = decs
        self.norm = norm

    def __hash__(self):
        return hash((self.lead, self.norm))


class BlochFuncSet:
    """A datatype that stores a set of bloch functions.

    Let's say "states" is a BlochFuncSet, then it has the following
    fields/attributes:

    states.hashtable: dict

        a dictionary that maps any product state in the Hilbert space
        to the full Bloch state it is a part of

    BlochFuncSet is also iterable and could be indexed.

    states[i]: BlochFunc
        BlochFunc is another datatype defined above and has the following
        fields/attributes:

        states[i].lead: int

            the leading product state of a Bloch state

        states[i].decs: numpy.array

            the full Bloch state in a 2-D array. Traversal along the array
            rows amounts to translation in the x-direction on the lattice and
            traversal along the array columns amouns to translation in the
            y-direction on the lattice. The 0th element is the leading state.
    """

    def __init__(self, bfuncs):
        """
        Parameters
        --------------------
        bfuncs: list
            list of BlochFunc's
        """
        self.data = bfuncs
        self.hashtable = {}
        self._populate_dict()
        self.nonzero = None

    def __getitem__(self, i):
        return self.data[i]

    def __len__(self):
        return self.nonzero

    def _populate_dict(self):
        for bfunc in self.data:
            for dec in bfunc.decs.keys():
                self.hashtable[dec] = bfunc

    def sort(self):
        self.data = sorted(self.data, key=lambda x: x.lead)


@functools.lru_cache(maxsize=None)
def _translate_x_aux(Nx, Ny):
    n = np.arange(0, Nx * Ny, Nx)
    a = 2 ** (n + Nx)
    b = 2 ** n
    c = 2 ** Nx
    d = 2 ** (Nx - 1)
    e = 2 ** n
    return a, b, c, d, e


def translate_x(dec, Nx, Ny):
    """translates a given state along the x-direction for one site.
    assumes periodic boundary condition.

    Parameters
    --------------------
    dec: int
        the decimal representation of a product state.
    Nx: int
        lattice size along the x-direction
    Ny: int
        lattice size along the y-direction

    Returns
    --------------------
    dec': int
        the new state after translation
    """
    a, b, c, d, e = _translate_x_aux(Nx, Ny)
    s = dec % a // b    # "%" is modulus and "//" is integer division
    s = (s * 2) % c + s // d
    return (e).dot(s)


@functools.lru_cache(maxsize=None)
def _translate_y_aux(Nx, Ny):
    return 2 ** Nx, 2 ** (Nx * (Ny - 1))


def translate_y(dec, Nx, Ny):
    """translates a given state along the y-direction for one site.
    assumes periodic boundary condition.

    Parameters
    --------------------
    dec: int
        the decimal representation of a product state.
    Nx: int
        lattice size along the x-direction
    Ny: int
        lattice size along the y-direction

    Returns
    --------------------
    dec': int
        the new state after translation
    """
    xdim, pred_totdim = _translate_y_aux(Nx, Ny)
    tail = dec % xdim
    return dec // xdim + tail * pred_totdim


def _exchange_spin_flips(dec, s1, s2):
    """tests whether a given state constains a spin flip at sites
    represented by s1 and s2.

    Parameters
    --------------------
    dec: int
        the decimal representation of a product state.
    s1: int
        the decimal representation of bit 1 to be examined
    s2: int
        the decimal representation of bit 2 to be examined

    Returns
    --------------------
    updown: bool
    downup: bool
    """
    updown = downup = False
    if (dec | s1 == dec) and (not dec | s2 == dec):
        updown = True
    if (not dec | s1 == dec) and (dec | s2 == dec):
        downup = True
    return updown, downup


def _repeated_spins(dec, s1, s2):
    """tests whether both spins at s1 and s2 point in the same direction.

    Parameters
    --------------------
    dec: int
        the decimal representation of a product state.
    s1: int
        the decimal representation of bit 1 to be examined
    s2: int
        the decimal representation of bit 2 to be examined

    Returns
    --------------------
    upup: bool
    downdown: bool
    """
    upup = downdown = False
    if (dec | s1 == dec) and (dec | s2 == dec):
        upup = True
    if (not dec | s1 == dec) and (not dec | s2 == dec):
        downdown = True
    return upup, downdown


@functools.lru_cache(maxsize=None)
def _phase_arr(Nx, Ny, kx, ky):
    """generate an array of phases that maps to an array of product states.

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone

    Returns
    --------------------
    phase_arr: numpy.array
    """
    m = np.arange(Nx)
    n = np.arange(Ny)
    xphase = np.exp(2j * np.pi * kx * m / Nx)
    yphase = np.exp(2j * np.pi * ky * n / Ny)
    return np.outer(yphase, xphase)


def _norm_coeff(bfunc, Nx, Ny, kx, ky):
    """generates the norm of a given configuration, akin to the reciprocal
    of the normalization factor.

    Parameters
    --------------------
    bfunc: BlochFunc
        a given bloch state
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone

    Returns
    --------------------
    coeff: float
        the normalization factor
    """
    phase_arr = _phase_arr(Nx, Ny, kx, ky)
    phases = []
    for locs in bfunc.decs.values():
        rows, cols = tuple(zip(*locs))
        phases.append(np.sum(phase_arr[rows, cols]))
    return np.linalg.norm(phases)


@functools.lru_cache(maxsize=None)
def _gamma(Nx, Ny, s1, s2):
    """calculates γ"""
    m = int(round(np.log2(s1)))
    n = int(round(np.log2(s2)))
    vec1 = SiteVector.from_index(m, Nx, Ny)
    vec2 = SiteVector.from_index(n, Nx, Ny)
    ang = vec1.angle_with(vec2)
    return np.exp(1j * ang)


@functools.lru_cache(maxsize=None)
def _interacting_sites(Nx, Ny, l):
    """generates the integers that represent interacting sites

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    l: int
        range of interaction. 1 for nearest neighbor interation, 2 for
        second neighbors, 3 for third neighbors

    Returns
    --------------------
    site1: numpy.array
        array of integers that are decimal representations of single sites
    site2: numpy.array
        array of integers that are decimal representations of sites that
        when taken together (zip) with bit1 locates interacting sites
    """
    site1, site2 = [], []
    bond_orders = _generate_bonds(Nx, Ny)
    bonds = bond_orders[l - 1]
    for bond in bonds:
        site1.append(bond[0].lattice_index)
        site2.append(bond[1].lattice_index)
    site1 = np.array(site1)
    site2 = np.array(site2)
    return 2 ** site1, 2 ** site2


@functools.lru_cache(maxsize=1)
def zero_momentum_states(Nx, Ny):
    """finds a full set of bloch states with zero lattice momentum

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction

    Returns
    --------------------
    states: BlochFuncSet
    """
    def find_T_invariant_set(dec):
        """takes an integer "dec" as the leading state and translates it
        repeatedly along the x and y-directions until we find the entire
        set
        """
        decs = {}
        new_dec = dec
        for n in range(Ny):
            for m in range(Nx):
                sieve[new_dec] = 0
                try:
                    decs[new_dec].append((n, m))
                except KeyError:
                    decs[new_dec] = [(n, m)]
                new_dec = translate_x(new_dec, Nx, Ny)
            new_dec = translate_y(new_dec, Nx, Ny)
        return BlochFunc(lead=dec, decs=decs)

    N = Nx * Ny
    sieve = np.ones(2 ** N, dtype=np.int8)
    bfuncs = []
    for dec in range(2 ** N):
        if sieve[dec]:
            bfuncs.append(find_T_invariant_set(dec))
    table = BlochFuncSet(bfuncs)
    table.sort()
    return table


@functools.lru_cache(maxsize=1)
def _bloch_states(Nx, Ny, kx, ky):
    """prunes the zero-momentum set and returns a reduced basis set that
    consists of only basis states that are non-zero in the given
    momentum configuration

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone

    Returns
    --------------------
    table: BlochFuncSet
    """
    bfuncs = zero_momentum_states(Nx, Ny)
    nonzero = 0
    for bfunc in bfuncs:
        norm = _norm_coeff(bfunc, Nx, Ny, kx, ky)
        bfunc.norm = norm
        if norm > 1e-8:
            nonzero += 1
    bfuncs.nonzero = nonzero
    return bfuncs


@functools.lru_cache(maxsize=None)
def _find_leading_state(Nx, Ny, kx, ky, dec):
    """finds the leading state for a given state

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    dec: int
        the decimal representation of a product state.

    Returns
    --------------------
    cntd_state: BlochFunc
        the connected bloch state associated with the given decimal
    phase: float
        the phase associated with the given state
    """
    bloch_states = _bloch_states(Nx, Ny, kx, ky)
    cntd_state = bloch_states.hashtable[dec]

    if cntd_state.norm < 1e-8:
        raise exceptions.NotFoundError

    # trace how far the given state is from the leading state by translation
    phase_arr = _phase_arr(Nx, Ny, kx, ky)
    rows, cols = tuple(zip(*cntd_state.decs[dec]))
    phase = np.sum(phase_arr[rows, cols]).conjugate()
    phase /= np.abs(phase)
    return cntd_state, phase


@functools.lru_cache(maxsize=1)
def _gen_ind_dec_conv_dicts(Nx, Ny, kx, ky):
    states = _bloch_states(Nx, Ny, kx, ky)
    nonzero_states = [s for s in states if s.norm > 1e-8]
    dec = [bfunc.lead for bfunc in nonzero_states]
    nstates = len(dec)
    inds = list(range(nstates))
    dec_to_ind = dict(zip(dec, inds))
    ind_to_dec = dict(zip(inds, nonzero_states))
    return ind_to_dec, dec_to_ind


def _coeff(Nx, Ny, kx, ky, orig_state, cntd_state):
    """calculates the coefficient that the two given states (sans phase)

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    orig_state: BlochFunc
        the state that H acts on
    cntd_state: BlochFunc
        the state that H connects orig_state to

    Returns
    --------------------
    coeff: float
    """
    coeff = cntd_state.norm / orig_state.norm
    return coeff


def H_z_elements(Nx, Ny, kx, ky, i, l):
    """computes the Hz elements

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    i: int
        index of the Bloch state before the Hamiltonian acts on it
    l: int
        range of interaction. 1 for nearest neighbor interation, 2 for
        second neighbors, 3 for third neighbors

    Returns
    --------------------
    H_ii: float
        the i'th element of Hz
    """
    ind_to_dec, dec_to_ind = _gen_ind_dec_conv_dicts(Nx, Ny, kx, ky)
    state = ind_to_dec[i]
    site1, site2 = _interacting_sites(Nx, Ny, l)
    same_dir = 0
    # s1 is the decimal representation of a spin-up at site 1 and
    # s2 is the decimal representation of a spin-up at site 2
    for s1, s2 in zip(site1, site2):
        upup, downdown = _repeated_spins(state.lead, s1, s2)
        same_dir += upup + downdown
    diff_dir = len(site1) - same_dir
    return 0.25 * (same_dir - diff_dir)


def H_pm_elements(Nx, Ny, kx, ky, i, l):
    """computes the H+- elements

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    i: int
        index of the Bloch state before the Hamiltonian acts on it
    l: int
        range of interaction. 1 for nearest neighbor interation, 2 for
        second neighbors, 3 for third neighbors

    Returns
    --------------------
    j_element: dict
        a dictionary that maps j's to their values for a given i
    """
    ind_to_dec, dec_to_ind = _gen_ind_dec_conv_dicts(Nx, Ny, kx, ky)
    orig_state = ind_to_dec[i]
    j_element = {}
    sites = _interacting_sites(Nx, Ny, l)
    # s1 is the decimal representation of a spin-up at site 1 and
    # s2 is the decimal representation of a spin-up at site 2
    for s1, s2 in zip(*sites):
        # updown and downup are booleans
        updown, downup = _exchange_spin_flips(orig_state.lead, s1, s2)
        if updown or downup:
            # if the configuration is updown, we flip the spins by
            # turning the spin-up to spin-down and vice versa
            if updown:  # if updown == True
                new_dec = orig_state.lead - s1 + s2
            elif downup:  # if downup == True
                new_dec = orig_state.lead + s1 - s2

            try:
                # find what connected state it is if the state we got from bit-
                #  flipping is not in our records
                cntd_state, phase = _find_leading_state(Nx, Ny, kx, ky, new_dec)
                # once we have the leading state, we proceed to find the
                # corresponding matrix index
                j = dec_to_ind[cntd_state.lead]
                # total coefficient is phase * sqrt(whatever)
                coeff = phase * _coeff(Nx, Ny, kx, ky, orig_state, cntd_state)
                try:
                    j_element[j] += coeff
                except KeyError:
                    j_element[j] = coeff
            except exceptions.NotFoundError:  # connecting to a zero state
                pass
    return j_element


def H_ppmm_elements(Nx, Ny, kx, ky, i, l):
    """computes the H++-- elements

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    i: int
        index of the Bloch state before the Hamiltonian acts on it
    l: int
        range of interaction. 1 for nearest neighbor interation, 2 for
        second neighbors, 3 for third neighbors

    Returns
    --------------------
    j_element: dict
        a dictionary that maps j's to their values for a given i
    """
    ind_to_dec, dec_to_ind = _gen_ind_dec_conv_dicts(Nx, Ny, kx, ky)
    orig_state = ind_to_dec[i]
    j_element = {}
    sites = _interacting_sites(Nx, Ny, l)
    for s1, s2 in zip(*sites):
        upup, downdown = _repeated_spins(orig_state.lead, s1, s2)
        if upup or downdown:
            if upup:
                new_dec = orig_state.lead - s1 - s2
                γ = _gamma(Nx, Ny, s1, s2).conjugate()
            elif downdown:
                new_dec = orig_state.lead + s1 + s2
                γ = _gamma(Nx, Ny, s1, s2)

            try:
                cntd_state, phase = _find_leading_state(Nx, Ny, kx, ky, new_dec)
                j = dec_to_ind[cntd_state.lead]
                coeff = phase * _coeff(Nx, Ny, kx, ky, orig_state, cntd_state)
                try:
                    j_element[j] += coeff * γ
                except KeyError:
                    j_element[j] = coeff * γ
            except exceptions.NotFoundError:
                pass
    return j_element


def H_pmz_elements(Nx, Ny, kx, ky, i, l):
    """computes the H+-z elements

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    i: int
        index of the Bloch state before the Hamiltonian acts on it
    l: int
        range of interaction. 1 for nearest neighbor interation, 2 for
        second neighbors, 3 for third neighbors

    Returns
    --------------------
    j_element: dict
        a dictionary that maps j's to their values for a given i
    """
    ind_to_dec, dec_to_ind = _gen_ind_dec_conv_dicts(Nx, Ny, kx, ky)
    orig_state = ind_to_dec[i]
    j_element = {}
    sites = _interacting_sites(Nx, Ny, l)
    for s1, s2 in zip(*sites):
        for _ in range(2):
            if orig_state.lead | s1 == orig_state.lead:
                z_contrib = 0.5
            else:
                z_contrib = -0.5

            if orig_state.lead | s2 == orig_state.lead:
                new_dec = orig_state.lead - s2
                γ = _gamma(Nx, Ny, s1, s2).conjugate()
            else:
                new_dec = orig_state.lead + s2
                γ = -_gamma(Nx, Ny, s1, s2)

            try:
                cntd_state, phase = _find_leading_state(Nx, Ny, kx, ky, new_dec)
                j = dec_to_ind[cntd_state.lead]
                coeff = phase * _coeff(Nx, Ny, kx, ky, orig_state, cntd_state)
                try:
                    j_element[j] += z_contrib * γ * coeff
                except KeyError:
                    j_element[j] = z_contrib * γ * coeff
            except exceptions.NotFoundError:
                pass

            # switch sites 1 and 2 and repeat
            s1, s2 = s2, s1
    return j_element


def _diag_components(Nx, Ny, kx, ky, l, func):
    """constructs the Hz matrix by calling the H_z_elements function while
    looping over all available i's
    """
    n = len(_bloch_states(Nx, Ny, kx, ky))
    data = np.empty(n)
    for i in range(n):
        data[i] = func(Nx, Ny, kx, ky, i, l)
    inds = np.arange(n)
    return sparse.csc_matrix((data, (inds, inds)), shape=(n, n))


@utils.cache.matcache
def H_z_matrix(Nx, Ny, kx, ky, l):
    return _diag_components(Nx, Ny, kx, ky, l, H_z_elements)


def _offdiag_components(Nx, Ny, kx, ky, l, func):
    """constructs the H+-, H++-- and H+-z matrices by calling their
    corresponding functions while looping over all available i's
    """
    n = len(_bloch_states(Nx, Ny, kx, ky))
    row, col, data = [], [], []
    for i in range(n):
        j_elements = func(Nx, Ny, kx, ky, i, l)
        for j, element in j_elements.items():
            row.append(i)
            col.append(j)
            data.append(element)
    return sparse.csc_matrix((data, (row, col)), shape=(n, n))


@utils.cache.matcache
def H_pm_matrix(Nx, Ny, kx, ky, l):
    return _offdiag_components(Nx, Ny, kx, ky, l, H_pm_elements)


@utils.cache.matcache
def H_ppmm_matrix(Nx, Ny, kx, ky):
    l = 1
    return _offdiag_components(Nx, Ny, kx, ky, l, H_ppmm_elements)


@utils.cache.matcache
def H_pmz_matrix(Nx, Ny, kx, ky):
    l = 1
    return 1j * _offdiag_components(Nx, Ny, kx, ky, l, H_pmz_elements)


def hamiltonian_consv_k(Nx, Ny, kx, ky, J_pm=0, J_z=0, J_ppmm=0, J_pmz=0, J2=0, J3=0):
    """construct the full Hamiltonian matrix in the given momentum configuration

    Parameters
    --------------------
    Nx: int
        lattice length in the x-direction
    Ny: int
        lattice length in the y-direction
    kx: int
        the x-component of lattice momentum * Nx / 2π in a (-π, +π]
        Brillouin zone
    ky: int
        the y-component of lattice momentum * Ny / 2π in a (-π, +π]
        Brillouin zone
    J_pm: int/float
        the J+- parameter (defaults to 0)
    J_z: int/float
        the Jz parameter (defaults to 0)
    J_ppmm: int/float
        the J++-- parameter (defaults to 0)
    J_pmz: int/float
        the J+-z parameter (defaults to 0)
    J2: int/float
        the J2 parameter (defaults to 0)
    J3: int/float
        the J3 parameter (defaults to 0)

    Returns
    --------------------
    H: scipy.sparse.csc_matrix
    """
    H_z1 = H_z_matrix(Nx, Ny, kx, ky, 1)
    H_pm1 = H_pm_matrix(Nx, Ny, kx, ky, 1)
    H_ppmm = H_ppmm_matrix(Nx, Ny, kx, ky)
    H_pmz = H_pmz_matrix(Nx, Ny, kx, ky)
    nearest_neighbor_terms = J_pm * H_pm1 + J_z * H_z1 + J_ppmm * H_ppmm + J_pmz * H_pmz
    second_neighbor_terms = third_neighbor_terms = 0
    if not J2 == 0:
        H_z2 = H_z_matrix(Nx, Ny, kx, ky, 2)
        H_pm2 = H_pm_matrix(Nx, Ny, kx, ky, 2)
        second_neighbor_terms = J2 * (H_pm2 + J_z / J_pm * H_z2)
    if not J3 == 0:
        H_z3 = H_z_matrix(Nx, Ny, kx, ky, 3)
        H_pm3 = H_pm_matrix(Nx, Ny, kx, ky, 3)
        third_neighbor_terms = J3 * (H_pm3 + J_z / J_pm * H_z3)
    # clears cache so the computer doesn't run out of memory
    _find_leading_state.cache_clear()
    return nearest_neighbor_terms + second_neighbor_terms + third_neighbor_terms
