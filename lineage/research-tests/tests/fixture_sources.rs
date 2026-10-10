mod common;

#[test]
fn fixture_copies_are_byte_identical_to_the_research_originals() {
    // The research checkout must sit next to this repository (the build
    // depends on it). Every copied proof equals its frozen original.
    let research =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../ZWA-Lineage-Research");
    let steps = common::manifest()["steps"].as_object().unwrap();
    assert_eq!(steps.len(), 12);
    for (name, s) in steps {
        let original = std::fs::read(research.join(s["source"].as_str().unwrap())).unwrap();
        assert_eq!(original, common::step(name).proof, "{name}");
    }
}
