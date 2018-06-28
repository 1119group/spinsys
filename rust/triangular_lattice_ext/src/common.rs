use std::mem;
use libc::size_t;
use num_complex::Complex;
use fnv::FnvHashMap;

use sitevector::SiteVector;
use blochfunc::{BlochFunc, BlochFuncSet};
use super::POW2;

// c compatible complex type for export to numpy at the end
#[repr(C)]
pub struct CComplex<T> {
    pub re: T,
    pub im: T
}

impl<T> CComplex<T> {
    pub fn from_num_complex(c: Complex<T>) -> CComplex<T> {
        let re = c.re;
        let im = c.im;
        CComplex { re, im }
    }
}

#[repr(C)]
pub struct Vector<T> {
    pub ptr: *mut T,
    pub len: size_t
}

impl<T> Vector<T> {
    fn new(ptr: *mut T, len: size_t) -> Vector<T> {
        Vector { ptr, len }
    }
}

#[repr(C)]
pub struct CoordMatrix<T> {
    pub data: Vector<T>,
    pub col: Vector<u32>,
    pub row: Vector<u32>,
    pub ncols: u32,
    pub nrows: u32
}

impl<T> CoordMatrix<T> {
    pub fn new(mut data: Vec<T>, mut col: Vec<u32>, mut row: Vec<u32>,
               ncols: u32, nrows: u32) -> CoordMatrix<T> {
        let data_ptr = data.as_mut_ptr();
        let data_len = data.len() as size_t;

        let col_ptr = col.as_mut_ptr();
        let col_len = col.len() as size_t;

        let row_ptr = row.as_mut_ptr();
        let row_len = row.len() as size_t;

        mem::forget(data);
        mem::forget(col);
        mem::forget(row);
        let data = Vector::new(data_ptr, data_len);
        let col = Vector::new(col_ptr, col_len);
        let row = Vector::new(row_ptr, row_len);
        CoordMatrix { data, col, row, ncols, nrows }
    }
}

pub fn translate_x(dec: u64, nx: u32, ny: u32) -> u64 {
    let n = (0..ny).map(|x| x * nx).collect::<Vec<u32>>();
    let s = n.iter()
        .map(|&x| dec % POW2[(x + nx) as usize] / POW2[x as usize])
        .map(|x| (x * 2_u64) % POW2[nx as usize] + x / POW2[nx as usize - 1]);

    n.iter().map(|&x| POW2[x as usize])
        .zip(s)
        .map(|(a, b)| a * b)  // basically a dot product here
        .sum()
}

pub fn translate_y(dec: u64, nx: u32, ny: u32) -> u64 {
    let xdim = POW2[nx as usize];
    let pred_totdim = POW2[nx as usize * (ny - 1) as usize];
    let tail = dec % xdim;
    dec / xdim + tail * pred_totdim
}

pub fn exchange_spin_flips(dec: u64, s1: u64, s2: u64) -> (bool, bool) {
    let updown = (dec | s1 == dec) && (dec | s2 != dec);
    let downup = (dec | s1 != dec) && (dec | s2 == dec);
    (updown, downup)
}

pub fn repeated_spins(dec: u64, s1: u64, s2: u64) -> (bool, bool) {
    let upup = (dec | s1 == dec) && (dec | s2 == dec);
    let downdown = (dec | s1 != dec) && (dec | s2 != dec);
    (upup, downdown)
}

pub fn generate_bonds(nx: u32, ny: u32) -> Vec<Vec<Vec<SiteVector>>> {
    let n = nx * ny;
    let mut vec = SiteVector::new((0, 0), nx, ny);
    let mut bonds_by_range = vec![Vec::new(); 3];
    for _ in 0..n {
        let nearest_neighbor = vec.nearest_neighboring_sites(false);
        let second_neighbor = vec.second_neighboring_sites(false);
        let third_neighbor = vec.third_neighboring_sites(false);
        let neighbors = vec![nearest_neighbor, second_neighbor, third_neighbor];
        for (leap, bonds) in bonds_by_range.iter_mut().enumerate() {
            for n in neighbors[leap].iter() {
                let mut bond = vec![vec.clone(), n.clone()];
                bond.sort();
                bonds.push(bond);
            }
        }
        vec = vec.next_site();
    }
    bonds_by_range
}

pub fn gamma(nx: u32, ny: u32, s1: u64, s2: u64) -> Complex<f64> {
    let m = (s1 as f64).log2().round() as u32;
    let n = (s2 as f64).log2().round() as u32;
    let vec1 = SiteVector::from_index(m, nx, ny);
    let vec2 = SiteVector::from_index(n, nx, ny );
    let ang = vec1.angle_with(&vec2);

    Complex::from_polar(&1.0, &ang)
}

/// Generate all possible pairs of interacting sites on the lattice according to
/// the stride l
pub fn interacting_sites(nx: u32, ny: u32, l: u32) -> (Vec<u64>, Vec<u64>) {
    let mut site1 = Vec::new();
    let mut site2 = Vec::new();
    let bonds_by_range = generate_bonds(nx, ny);
    let bonds = &bonds_by_range[l as usize - 1];
    for bond in bonds.iter() {
        site1.push(bond[0].lattice_index());
        site2.push(bond[1].lattice_index());
    }

    (
    site1.into_iter().map(|x| 2_u64.pow(x)).collect::<Vec<_>>(),
    site2.into_iter().map(|x| 2_u64.pow(x)).collect::<Vec<_>>(),
    )
}

pub fn triangular_vert_sites(nx: u32, ny: u32) -> (Vec<u64>, Vec<u64>, Vec<u64>) {
    let mut site1 = Vec::new();
    let mut site2 = Vec::new();
    let mut site3 = Vec::new();
    let mut vec = SiteVector::new((0, 0), nx, ny);

    for _ in 0..ny {
        for _ in 0..nx {
            // For ijk in clockwise direction in upright triangle
            let s1 = vec.lattice_index();
            let s2 = vec.xhop(1).lattice_index();
            let s3 = vec.xhop(1).yhop(1).lattice_index();
            site1.push(s1);
            site2.push(s2);
            site3.push(s3);

            // For ijk in clockwise direction in inverted triangle
            let s4 = vec.lattice_index();
            let s5 = vec.xhop(1).lattice_index();
            let s6 = vec.xhop(1).yhop(-1).lattice_index();
            site1.push(s4);
            site2.push(s5);
            site3.push(s6);

            vec = vec.xhop(1);
        }
        vec = vec.yhop(1);
    }

    (
    site1.into_iter().map(|s| 2_u64.pow(s)).collect::<Vec<u64>>(),
    site2.into_iter().map(|s| 2_u64.pow(s)).collect::<Vec<u64>>(),
    site3.into_iter().map(|s| 2_u64.pow(s)).collect::<Vec<u64>>()
    )
}

/// Generate all permutations of the combination of any two sites on the lattice
/// where l = |i - j| for sites i and j
pub fn all_sites(nx: u32, ny: u32, l: u32) -> (Vec<u64>, Vec<u64>) {
    let mut vec = SiteVector::new((0, 0), nx, ny);
    let xstride = (l % nx) as i32;
    let ystride = (l / nx) as i32;
    let mut site1 = Vec::new();
    let mut site2 = Vec::new();
    for _ in 0..ny {
        for _ in 0..nx {
            let s1 = vec.lattice_index();
            let s2 = vec.xhop(xstride).yhop(ystride).lattice_index();
            site1.push(s1);
            site2.push(s2);
            vec = vec.xhop(1);
        }
        vec = vec.yhop(1);
    }

    (
    site1.into_iter().map(|s| 2_u64.pow(s)).collect::<Vec<u64>>(),
    site2.into_iter().map(|s| 2_u64.pow(s)).collect::<Vec<u64>>()
    )
}

pub fn find_leading_state<'a>(dec: u64,
                              hashtable: &'a FnvHashMap<&u64, &BlochFunc>
                              ) -> Option<(&'a BlochFunc, Complex<f64>)> {

    match hashtable.get(&dec) {
        None => None,
        Some(&cntd_state) => match cntd_state.decs.get(&dec) {
            None     => None,
            Some(&p) => {
                let mut phase = p.conj();
                phase /= phase.norm();
                Some((cntd_state, phase))
            },
        }
    }
}

pub fn gen_ind_dec_conv_dicts<'a>(bfuncs: &'a BlochFuncSet)
    -> (FnvHashMap<u32, &'a BlochFunc>, FnvHashMap<u64, u32>) {
    let dec = bfuncs.iter()
        .map(|x| x.lead)
        .collect::<Vec<_>>();
    let nstates = dec.len();
    let inds = (0..nstates as u32).collect::<Vec<u32>>();

    // build the hashtables
    let dec_to_ind = dec.into_iter()
        .zip(inds.clone())
        .collect::<FnvHashMap<u64, u32>>();
    let ind_to_dec = inds.into_iter()
        .zip(bfuncs.iter())
        .collect::<FnvHashMap<u32, &BlochFunc>>();

    (ind_to_dec, dec_to_ind)
}

pub fn coeff(orig_state: &BlochFunc, cntd_state: &BlochFunc) -> f64 {
    cntd_state.norm / orig_state.norm
}
