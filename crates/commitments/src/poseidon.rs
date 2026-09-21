//! Poseidon over BN254 with circomlib parameters.
//!
//! The frozen Phase 0 circuits hash with `circomlib`'s `Poseidon(t)` and the
//! Phase 0 fixtures were produced by `circomlibjs` 0.1.7. `light-poseidon`'s
//! `new_circom` constructor uses those same BN254 parameters. Compatibility is
//! pinned by `tests/poseidon_compat.rs` for every arity the circuits use. Any
//! replacement must reproduce those vectors exactly.

use ark_bn254::Fr;
use ark_ff::{BigInteger, PrimeField};
use light_poseidon::{Poseidon, PoseidonHasher};
use zwa_protocol::{FieldElement, ProtocolError, Result};

/// Converts a canonical protocol field element into an arkworks BN254 scalar.
///
/// `FieldElement` is always reduced, so this is a representation change and
/// never a modular reduction.
fn to_fr(value: FieldElement) -> Fr {
    Fr::from_be_bytes_mod_order(&value.to_be_bytes())
}

/// Converts an arkworks BN254 scalar into a canonical protocol field element.
fn from_fr(value: Fr) -> FieldElement {
    let bytes = value.into_bigint().to_bytes_be();
    // An `Fr` is always reduced and its big-endian encoding is always exactly
    // 32 bytes, so the canonical-encoding check here is unreachable.
    FieldElement::from_be_slice(&bytes)
        .expect("an arkworks BN254 scalar is always a canonical reduced 32-byte value")
}

/// Hashes `inputs` with circomlib-parameter Poseidon over BN254.
///
/// # Errors
///
/// Returns [`ProtocolError::UnsupportedPoseidonArity`] for an arity outside the
/// supported `1..=12` range. The frozen circuits only use arities 2 through 5.
pub fn poseidon(inputs: &[FieldElement]) -> Result<FieldElement> {
    let arity = inputs.len();
    let mut hasher = Poseidon::<Fr>::new_circom(arity)
        .map_err(|_| ProtocolError::UnsupportedPoseidonArity { arity })?;
    let scalars: Vec<Fr> = inputs.iter().copied().map(to_fr).collect();
    let digest = hasher
        .hash(&scalars)
        .map_err(|_| ProtocolError::UnsupportedPoseidonArity { arity })?;
    Ok(from_fr(digest))
}

/// Hashes a compile-time-sized input array.
///
/// Every protocol call site passes a fixed array of 2 to 5 elements, all of
/// which [`poseidon`] supports, so the arity error is unreachable here. Keeping
/// the staging infallible is what lets the commitment functions stay pure
/// total functions.
pub(crate) fn hash<const N: usize>(inputs: [FieldElement; N]) -> FieldElement {
    poseidon(&inputs).expect("the frozen commitment staging only uses Poseidon arities 2 through 5")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unsupported_arities_are_rejected_without_panicking() {
        assert_eq!(
            poseidon(&[]),
            Err(ProtocolError::UnsupportedPoseidonArity { arity: 0 })
        );
        let too_many = vec![FieldElement::ZERO; 13];
        assert_eq!(
            poseidon(&too_many),
            Err(ProtocolError::UnsupportedPoseidonArity { arity: 13 })
        );
    }

    #[test]
    fn field_conversion_round_trips() {
        for decimal in [
            "0",
            "1",
            "21888242871839275222246405745257275088548364400416034343698204186575808495616",
            "10187400613857124614980227259922066295752635539032972479692659299555113110306",
        ] {
            let element = FieldElement::from_decimal_str(decimal).unwrap();
            assert_eq!(from_fr(to_fr(element)), element);
        }
    }
}
