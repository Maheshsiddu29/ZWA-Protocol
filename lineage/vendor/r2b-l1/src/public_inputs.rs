//! Frozen public instance. Field order matches r2a1::PublicInputsV2.
use group::ff::PrimeField;
use pasta_curves::pallas;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PublicInputIndex {
    CmxOld = 0,
    NfOld = 1,
    CmxNew = 2,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PublicInputsV2 {
    pub cmx_old: [u8; 32],
    pub nf_old: [u8; 32],
    pub cmx_new: [u8; 32],
}

pub const CMX_OLD: usize = PublicInputIndex::CmxOld as usize;
pub const NF_OLD: usize = PublicInputIndex::NfOld as usize;
pub const CMX_NEW: usize = PublicInputIndex::CmxNew as usize;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PublicInstance([pallas::Base; 3]);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NonCanonical(pub PublicInputIndex);

impl PublicInstance {
    pub fn parse(p: &PublicInputsV2) -> Result<Self, NonCanonical> {
        let field = |b: &[u8; 32], i| {
            Option::<pallas::Base>::from(pallas::Base::from_repr(*b)).ok_or(NonCanonical(i))
        };
        Ok(PublicInstance([
            field(&p.cmx_old, PublicInputIndex::CmxOld)?,
            field(&p.nf_old, PublicInputIndex::NfOld)?,
            field(&p.cmx_new, PublicInputIndex::CmxNew)?,
        ]))
    }

    pub fn column(&self) -> Vec<pallas::Base> {
        self.0.to_vec()
    }
}
