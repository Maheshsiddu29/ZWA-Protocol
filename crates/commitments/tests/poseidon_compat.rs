//! Poseidon parameter compatibility gate.
//!
//! The expected digests below were produced by the pinned Phase 0 JavaScript
//! dependency `circomlibjs` 0.1.7 (`package.json`), which is the same
//! implementation that generated `tests/fixtures/*.json` and matches
//! `circomlib`'s `Poseidon(t)` used by the Circom circuits through
//! `circuits/shared/zk-origin/poseidon.circom`.
//!
//! If any assertion here fails, the Rust Poseidon dependency does not use the
//! BN254/circomlib parameters the frozen circuits require and must not be used.
//! The fix is never to change these expectations.

#![allow(clippy::unwrap_used)]

use zwa_commitments::poseidon;
use zwa_protocol::FieldElement;

fn field(decimal: &str) -> FieldElement {
    FieldElement::from_decimal_str(decimal).unwrap()
}

fn assert_digest(inputs: &[&str], expected: &str) {
    let values: Vec<FieldElement> = inputs.iter().copied().map(field).collect();
    let digest = poseidon(&values).unwrap();
    assert_eq!(
        digest.to_decimal_string(),
        expected,
        "circomlibjs Poseidon({}) mismatch",
        inputs.len()
    );
}

#[test]
fn poseidon2_matches_circomlibjs() {
    assert_digest(
        &["1", "2"],
        "7853200120776062878684798364095072458815029376092732009249414926327459813530",
    );
    assert_digest(
        &["0", "0"],
        "14744269619966411208579211824598458697587494354926760081771325075741142829156",
    );
    // Phase 0G `credential.subjectCommitment` = H(SUBJECT1, subjectSecret).
    assert_digest(
        &["6004778564925477937", "77112233445566778899"],
        "8182499163832458428983635402341439692935005683808059285091898351261831993662",
    );
}

#[test]
fn poseidon3_matches_circomlibjs() {
    assert_digest(
        &["1", "2", "3"],
        "6542985608222806190361240322586112750744169038454362455181422643027100751666",
    );
    // Phase 0F `trade.offeredAssetCommitment` = H(ASSETV1, offeredHi, offeredLo).
    assert_digest(
        &[
            "18387490596738609",
            "1763751950854191318876944356107539572",
            "64732350615944551779799158410112239944",
        ],
        "5603161927002640027449724222840117891821179304468803879568701117743463235562",
    );
}

#[test]
fn poseidon4_matches_circomlibjs() {
    assert_digest(
        &["1", "2", "3", "4"],
        "18821383157269793795438455681495246036402687001665670618754263018637548127333",
    );
    // Phase 0F/0G `trade.feeCommitment`
    // = H(FEEV1, ZEC, matcherFeeAmount, matcherFeeRecipientCommitment).
    assert_digest(
        &[
            "301809882673",
            "5915971",
            "5",
            "1800273984094439421343257609634901689467303577600258601269976617936586404380",
        ],
        "9224703263949515639127683057768488660604225733035241220145439229774558899084",
    );
}

#[test]
fn poseidon5_matches_circomlibjs() {
    assert_digest(
        &["1", "2", "3", "4", "5"],
        "6183221330272524995739186171720101788151706631170188140075976616310159254464",
    );
    // The frozen Phase 0G V1 credential leaf, which Phase 1B superseded. It is
    // kept because it is a known-good circomlibjs arity-5 vector; the live
    // Phase 1B leaf is arity 6 and is pinned below.
    assert_digest(
        &[
            "18949280892933681",
            "70707070707070",
            "8182499163832458428983635402341439692935005683808059285091898351261831993662",
            "10862951632723896631943303993573880486210175684885551136634224834169439788480",
            "41",
        ],
        "15218271836826561578701755741896301127594556628574350368538319672252803018733",
    );
}

#[test]
fn poseidon6_matches_circomlibjs() {
    assert_digest(
        &["1", "2", "3", "4", "5", "6"],
        "20400040500897583745843009878988256314335038853985262692600694741116813247201",
    );
    // The Phase 1B active credential leaf
    // = H(CRED_V2, authorityCommitment, subjectCommitment, credentialMeta,
    //     credentialNonce, approvedReceiverCommitment), from
    // tests/fixtures/phase1b-eligibility-v2.json. Phase 1B introduced arity 6,
    // so these parameters must be pinned exactly like the others.
    assert_digest(
        &[
            "18949280892933682",
            "70707070707070",
            "8182499163832458428983635402341439692935005683808059285091898351261831993662",
            "10862951632723896631943303993573880486210175684885551136634224834169439788480",
            "41",
            "11061882383881915406447883142926671585417814924641945492950382527926270559758",
        ],
        "20863877480330145676905791210365306223555027381899343250780242783978115337129",
    );
}

#[test]
fn digests_are_deterministic_and_order_sensitive() {
    let a = field("1");
    let b = field("2");
    assert_eq!(poseidon(&[a, b]).unwrap(), poseidon(&[a, b]).unwrap());
    assert_ne!(poseidon(&[a, b]).unwrap(), poseidon(&[b, a]).unwrap());
    // Arity is part of the domain: padding with zero is not the same hash.
    assert_ne!(
        poseidon(&[a, b]).unwrap(),
        poseidon(&[a, b, FieldElement::ZERO]).unwrap()
    );
}
