//! Circuit witness in the gadget representation. Openings are the frozen V2
//! fields; this crate does not reintroduce the V1 rseed witness.
use group::{Curve, Group, GroupEncoding};
use pasta_curves::pallas;

#[derive(Clone, Copy, Debug)]
pub struct CircuitNote {
    pub g_d: pallas::Affine,
    pub pk_d: pallas::Affine,
    pub value: u64,
    pub rho: pallas::Base,
    pub psi: pallas::Base,
    pub rcm: pallas::Scalar,
}

#[derive(Clone, Copy, Debug)]
pub struct CircuitWitness {
    pub old: CircuitNote,
    pub new: CircuitNote,
    pub asset: pallas::Affine,
    pub nk: pallas::Base,
}

impl CircuitNote {
    pub fn to_affine_pair(self) -> (pallas::Affine, pallas::Affine) {
        (self.g_d, self.pk_d)
    }
}

pub fn affine_point(bytes: &[u8; 32]) -> Option<pallas::Affine> {
    Option::<pallas::Point>::from(pallas::Point::from_bytes(bytes))
        .filter(|point| !bool::from(point.is_identity()))
        .map(|point| point.to_affine())
}
