use halo2_proofs::{
    dev::{MockProver, VerifyFailure},
    plonk::{self, ProvingKey, SingleVerifier, VerifyingKey},
    poly::commitment::Params,
    transcript::{Blake2bRead, Blake2bWrite},
};
use pasta_curves::{pallas, vesta};
use rand::rngs::OsRng;

use crate::{
    circuit::lineage::{LineageCircuit, K},
    public_inputs::PublicInstance,
};

pub fn params() -> Params<vesta::Affine> {
    Params::new(K)
}

pub fn keygen_vk(params: &Params<vesta::Affine>) -> VerifyingKey<vesta::Affine> {
    plonk::keygen_vk(params, &LineageCircuit::default()).expect("keygen_vk")
}

pub fn keygen_pk(
    params: &Params<vesta::Affine>,
    vk: VerifyingKey<vesta::Affine>,
) -> ProvingKey<vesta::Affine> {
    plonk::keygen_pk(params, vk, &LineageCircuit::default()).expect("keygen_pk")
}

pub fn prove(
    params: &Params<vesta::Affine>,
    pk: &ProvingKey<vesta::Affine>,
    circuit: LineageCircuit,
    instance: &PublicInstance,
) -> Result<Vec<u8>, plonk::Error> {
    let column = instance.column();
    let instances: &[&[pallas::Base]] = &[&column];
    let mut transcript = Blake2bWrite::<_, vesta::Affine, _>::init(vec![]);
    plonk::create_proof(params, pk, &[circuit], &[instances], OsRng, &mut transcript)?;
    Ok(transcript.finalize())
}

pub fn verify(
    params: &Params<vesta::Affine>,
    vk: &VerifyingKey<vesta::Affine>,
    proof: &[u8],
    instance: &PublicInstance,
) -> bool {
    let column = instance.column();
    let instances: &[&[pallas::Base]] = &[&column];
    let strategy = SingleVerifier::new(params);
    let mut transcript = Blake2bRead::init(proof);
    plonk::verify_proof(params, vk, strategy, &[instances], &mut transcript).is_ok()
}

pub enum MockResult {
    Satisfied,
    Unsatisfied(Vec<VerifyFailure>),
    SynthesisError(plonk::Error),
}

pub fn mock(circuit: &LineageCircuit, instance: &PublicInstance) -> MockResult {
    match MockProver::run(K, circuit, vec![instance.column()]) {
        Err(e) => MockResult::SynthesisError(e),
        Ok(p) => match p.verify() {
            Ok(()) => MockResult::Satisfied,
            Err(f) => MockResult::Unsatisfied(f),
        },
    }
}
