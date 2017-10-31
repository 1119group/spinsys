import numpy as np
import spinsys


class SiteVector(spinsys.constructors.PeriodicBCSiteVector):

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


def hamiltonian(Nx, Ny, J_pm=1, J_z=1, J_ppmm=1, J_pmz=1):
    N = Nx * Ny

    σ_p = spinsys.constructors.raising()
    σ_m = spinsys.constructors.lowering()
    σz = spinsys.constructors.sigmaz()

    p_mats = [spinsys.half.full_matrix(σ_p, k, N) for k in range(N)]
    m_mats = [spinsys.half.full_matrix(σ_m, k, N) for k in range(N)]
    z_mats = [spinsys.half.full_matrix(σz, k, N) for k in range(N)]

    # Permute through all the nearest neighbor coupling bonds
    bonds = []
    vec = SiteVector((0, 0), Nx, Ny)
    for i in range(N):
        bonds.append((vec, vec.xhop(1)))
        bonds.append((vec, vec.yhop(1)))
        bonds.append((vec, vec.xhop(-1).yhop(1)))
        vec = vec.next_site()

    H_pm = H_z = H_ppmm = H_pmz = 0
    for bond in bonds:
        site1, site2 = bond
        i, j = site1.lattice_index, site2.lattice_index
        γ = np.exp(1j * site1.angle_with(site2))
        H_pm += p_mats[i].dot(m_mats[j]) + m_mats[i].dot(p_mats[j])
        H_z += z_mats[i].dot(z_mats[j])
        H_ppmm += γ * p_mats[i].dot(p_mats[j]) + \
            γ.conj() * m_mats[i].dot(m_mats[j])
        H_pmz += 1j * (γ.conj() * z_mats[i].dot(p_mats[j]) -
                       γ * z_mats[i].dot(m_mats[j]) +
                       γ.conj() * p_mats[i].dot(z_mats[j]) -
                       γ * m_mats[i].dot(z_mats[j]))
    return J_pm * H_pm + J_z * H_z + J_ppmm * H_ppmm + J_pmz * H_pmz