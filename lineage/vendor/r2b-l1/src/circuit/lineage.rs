//! R2B lineage circuit. Body is r2b/src/circuit/lineage.rs with one mechanical
//! rename: ZsaNoteCommitParams.is_native_asset -> is_zatoshi_asset, matching
//! Orchard cf801a5. The flag is still the constant 0, which selects Q_ZSA.
//!
//! The `NormalLineageStepV1` Halo2 circuit.
//!
//! Chip configuration mirrors the pinned ZSA Action circuit
//! (`orchard/src/circuit/circuit_zsa.rs:51-322`), minus everything the frozen
//! relation does not contain (Merkle path, CommitIvk, value commitment,
//! spend-auth rerandomisation). The two note commitments and the nullifier are
//! the pinned gadgets themselves (`note_commit.rs`, `derive_nullifier.rs`,
//! compiled in place). See `CIRCUIT.md` for the invariant-to-constraint map.

use group::ff::Field;
use group::Curve;
use halo2_gadgets::{
    ecc::{
        chip::{EccChip, EccConfig},
        NonIdentityPoint, ScalarFixed,
    },
    poseidon::{primitives as poseidon, Pow5Chip as PoseidonChip, Pow5Config as PoseidonConfig},
    sinsemilla::chip::{SinsemillaChip, SinsemillaConfig},
    utilities::{
        cond_swap::{CondSwapChip, CondSwapConfig},
        lookup_range_check::{LookupRangeCheck4_5BConfig, PallasLookupRangeCheck4_5BConfig},
    },
};
use halo2_proofs::{
    circuit::{floor_planner, AssignedCell, Layouter, Value},
    plonk::{self, Advice, Column, Constraints, Expression, Instance, Selector},
    poly::Rotation,
};
use orchard::{issuance::ReferenceKeys, note::AssetBase, value::NoteValue};
use pasta_curves::{arithmetic::CurveAffine, pallas};

use crate::{
    circuit::{
        derive_nullifier::{gadgets::derive_nullifier, ZsaNullifierParams},
        gadget::add_chip::{AddChip, AddConfig},
        note_commit::{
            gadgets::note_commit, NoteCommitChip, NoteCommitConfig, ZsaNoteCommitParams,
        },
    },
    constants::{OrchardCommitDomains, OrchardFixedBases, OrchardHashDomains},
    public_inputs::{CMX_NEW, CMX_OLD, NF_OLD},
    spec::diversify_hash,
    witness::CircuitWitness,
};

/// Circuit size parameter (`2^K` rows). Smallest value for which the real
/// R1.1 witness is satisfiable; see `BENCHMARK.md`.
pub const K: u32 = 11;

type Lookup = PallasLookupRangeCheck4_5BConfig;
type Sinsemilla =
    SinsemillaConfig<OrchardHashDomains, OrchardCommitDomains, OrchardFixedBases, Lookup>;

#[derive(Clone, Debug)]
pub struct Config {
    pub(in crate::circuit) primary: Column<Instance>,
    pub(in crate::circuit) advices: [Column<Advice>; 10],
    pub(in crate::circuit) q_lineage: Selector,
    pub(in crate::circuit) add_config: AddConfig,
    pub(in crate::circuit) ecc_config: EccConfig<OrchardFixedBases, Lookup>,
    pub(in crate::circuit) poseidon_config: PoseidonConfig<pallas::Base, 3, 2>,
    pub(in crate::circuit) cond_swap_config: CondSwapConfig,
    pub(in crate::circuit) sinsemilla_config_1: Sinsemilla,
    pub(in crate::circuit) sinsemilla_config_2: Sinsemilla,
    pub(in crate::circuit) old_note_commit_config: NoteCommitConfig<Lookup>,
    pub(in crate::circuit) new_note_commit_config: NoteCommitConfig<Lookup>,
}

/// `(x, y)` of the reference recipient's `g_d` and `pk_d`
/// (`ReferenceKeys::recipient()`, `orchard/src/constants/reference_keys.rs:14`).
pub(crate) fn reference_recipient_coords() -> [pallas::Base; 4] {
    let raw = ReferenceKeys::recipient().to_raw_address_bytes();
    let g_d = diversify_hash(&raw[..11].try_into().unwrap()).to_affine();
    let pk_d = Option::<crate::spec::NonIdentityPallasPoint>::from(
        crate::spec::NonIdentityPallasPoint::from_bytes(&raw[11..].try_into().unwrap()),
    )
    .expect("reference pk_d decodes")
    .to_affine();
    let g = g_d.coordinates().unwrap();
    let p = pk_d.coordinates().unwrap();
    [*g.x(), *g.y(), *p.x(), *p.y()]
}

pub(crate) fn native_asset_coords() -> [pallas::Base; 2] {
    let n = AssetBase::zatoshi().cv_base().to_affine();
    let c = n.coordinates().unwrap();
    [*c.x(), *c.y()]
}

pub(crate) fn inv_or_zero(x: pallas::Base) -> pallas::Base {
    Option::from(x.invert()).unwrap_or(pallas::Base::zero())
}

/// One `NormalLineageStepV1`. `Value::unknown()` for keygen.
#[derive(Clone, Debug, Default)]
pub struct LineageCircuit {
    pub witness: Value<CircuitWitness>,
}

impl LineageCircuit {
    pub fn new(w: CircuitWitness) -> Self {
        LineageCircuit {
            witness: Value::known(w),
        }
    }
}

pub(crate) fn free_advice<V: Copy>(
    mut layouter: impl Layouter<pallas::Base>,
    column: Column<Advice>,
    value: Value<V>,
) -> Result<AssignedCell<V, pallas::Base>, plonk::Error>
where
    for<'v> halo2_proofs::plonk::Assigned<pallas::Base>: From<&'v V>,
{
    layouter.assign_region(
        || "load private",
        |mut region| region.assign_advice(|| "load private", column, 0, || value),
    )
}

/// A cell fixed to a constant by an equality constraint to the constants
/// column. Used for `split_flag` and `is_native_asset`, which the frozen
/// relation fixes to `false`: neither is a witness.
pub(crate) fn constant_cell(
    mut layouter: impl Layouter<pallas::Base>,
    column: Column<Advice>,
    c: pallas::Base,
) -> Result<AssignedCell<pallas::Base, pallas::Base>, plonk::Error> {
    layouter.assign_region(
        || "constant",
        |mut region| region.assign_advice_from_constant(|| "constant", column, 0, c),
    )
}

impl plonk::Circuit<pallas::Base> for LineageCircuit {
    type Config = Config;
    type FloorPlanner = floor_planner::V1;

    fn without_witnesses(&self) -> Self {
        Self::default()
    }

    fn configure(meta: &mut plonk::ConstraintSystem<pallas::Base>) -> Config {
        // --- Columns and chips: as circuit_zsa.rs:53-305 ---------------------
        let advices = [(); 10].map(|_| meta.advice_column());

        let add_config = AddChip::configure(meta, advices[7], advices[8], advices[6]);

        let table_idx = meta.lookup_table_column();
        let table_range_check_tag = meta.lookup_table_column();
        let lookup = (
            table_idx,
            meta.lookup_table_column(),
            meta.lookup_table_column(),
        );

        let primary = meta.instance_column();
        meta.enable_equality(primary);
        for advice in advices.iter() {
            meta.enable_equality(*advice);
        }

        let lagrange_coeffs = [(); 8].map(|_| meta.fixed_column());
        let rc_a = lagrange_coeffs[2..5].try_into().unwrap();
        let rc_b = lagrange_coeffs[5..8].try_into().unwrap();
        meta.enable_constant(lagrange_coeffs[0]);

        let range_check = LookupRangeCheck4_5BConfig::configure_with_tag(
            meta,
            advices[9],
            table_idx,
            table_range_check_tag,
        );

        let ecc_config = EccChip::<OrchardFixedBases, Lookup>::configure(
            meta,
            advices,
            lagrange_coeffs,
            range_check,
        );

        let poseidon_config = PoseidonChip::configure::<poseidon::P128Pow5T3>(
            meta,
            advices[6..9].try_into().unwrap(),
            advices[5],
            rc_a,
            rc_b,
        );

        // circuit_zsa.rs takes its CondSwap config from MerkleChip; there is no
        // Merkle path here, so it is configured directly, on the same columns.
        let cond_swap_config = CondSwapChip::configure(meta, advices[..5].try_into().unwrap());

        let sinsemilla_config_1 = SinsemillaChip::configure(
            meta,
            advices[..5].try_into().unwrap(),
            advices[6],
            lagrange_coeffs[0],
            lookup,
            range_check,
            true,
        );
        let sinsemilla_config_2 = SinsemillaChip::configure(
            meta,
            advices[5..].try_into().unwrap(),
            advices[7],
            lagrange_coeffs[1],
            lookup,
            range_check,
            true,
        );

        let old_note_commit_config =
            NoteCommitChip::configure(meta, advices, sinsemilla_config_1.clone(), true);
        let new_note_commit_config =
            NoteCommitChip::configure(meta, advices, sinsemilla_config_2.clone(), true);

        // --- The lineage gate: N1 and N10 -----------------------------------
        //
        // row 0: v_old  inv_v  x(g_d)  inv  y(g_d)  inv  x(pk_d)  inv  y(pk_d)  inv
        // row 1: x(A)   inv    y(A)    inv  t
        //
        // Each `inv` is a free prover hint. A factor `(Δ·inv − 1)` can be made
        // zero iff `Δ != 0`; if `Δ == 0` the factor is `−1` whatever `inv` is.
        let q_lineage = meta.selector();
        let [gx, gy, px, py] = reference_recipient_coords();
        let [nx, ny] = native_asset_coords();
        meta.create_gate("NormalLineageStepV1: N1, N10", |meta| {
            let q = meta.query_selector(q_lineage);
            let cur = |meta: &mut plonk::VirtualCells<pallas::Base>, i: usize| {
                meta.query_advice(advices[i], Rotation::cur())
            };
            let next = |meta: &mut plonk::VirtualCells<pallas::Base>, i: usize| {
                meta.query_advice(advices[i], Rotation::next())
            };
            let one = Expression::Constant(pallas::Base::one());
            let nonzero = |d: Expression<pallas::Base>, inv: Expression<pallas::Base>| {
                d * inv - Expression::Constant(pallas::Base::one())
            };

            let v_old = cur(meta, 0);
            let v_inv = cur(meta, 1);
            let d_gx = cur(meta, 2) - Expression::Constant(gx);
            let d_gy = cur(meta, 4) - Expression::Constant(gy);
            let d_px = cur(meta, 6) - Expression::Constant(px);
            let d_py = cur(meta, 8) - Expression::Constant(py);
            let (i_gx, i_gy, i_px, i_py) = (cur(meta, 3), cur(meta, 5), cur(meta, 7), cur(meta, 9));

            let d_ax = next(meta, 0) - Expression::Constant(nx);
            let i_ax = next(meta, 1);
            let d_ay = next(meta, 2) - Expression::Constant(ny);
            let i_ay = next(meta, 3);
            let t = next(meta, 4);

            Constraints::with_selector(
                q,
                [
                    // N1a: v_old != 0 (v_old is 64-bit range-constrained by the
                    // NoteCommit decomposition, so this is v_old > 0).
                    ("N1 v_old != 0", v_old * v_inv - one),
                    // N1b: (g_d_old, pk_d_old) != reference recipient. At least
                    // one of the four coordinate differences is nonzero.
                    (
                        "N1 reference t",
                        t.clone() - nonzero(d_gx, i_gx) * nonzero(d_gy, i_gy),
                    ),
                    (
                        "N1 recipient != reference",
                        t * nonzero(d_px, i_px) * nonzero(d_py, i_py),
                    ),
                    // N10: asset != native, the pinned form
                    // (circuit_zsa.rs:164) with is_native_asset fixed to 0.
                    (
                        "N10 asset != native",
                        nonzero(d_ax, i_ax) * nonzero(d_ay, i_ay),
                    ),
                ],
            )
        });

        Config {
            primary,
            advices,
            q_lineage,
            add_config,
            ecc_config,
            poseidon_config,
            cond_swap_config,
            sinsemilla_config_1,
            sinsemilla_config_2,
            old_note_commit_config,
            new_note_commit_config,
        }
    }

    fn synthesize(
        &self,
        config: Config,
        mut layouter: impl Layouter<pallas::Base>,
    ) -> Result<(), plonk::Error> {
        SinsemillaChip::load(config.sinsemilla_config_1.clone(), &mut layouter)?;

        let ecc_chip = EccChip::<OrchardFixedBases, Lookup>::construct(
            config.ecc_config.clone(),
            // L1 halo2 requires an explicit version. AnchoredBase is the fixed
            // variable-base circuit. R2B does not call ScalarVar; the choice
            // still selects that chip rather than InsecureUnanchoredBase.
            halo2_gadgets::ecc::chip::CircuitVersion::AnchoredBase,
        );
        let cond_swap_chip = || CondSwapChip::construct(config.cond_swap_config.clone());
        let adv0 = config.advices[0];
        let w = self.witness;
        let old = w.map(|w| w.old);
        let new = w.map(|w| w.new);

        // ---- Private inputs -------------------------------------------------
        // N6: ONE AssetBase point, used by both commitments below.
        let asset = NonIdentityPoint::new(
            ecc_chip.clone(),
            layouter.namespace(|| "witness AssetBase"),
            w.map(|w| w.asset),
        )?;
        let nk = free_advice(layouter.namespace(|| "witness nk"), adv0, w.map(|w| w.nk))?;

        // N2 / N10 constant semantics: not witnesses.
        let split_flag = constant_cell(
            layouter.namespace(|| "split_flag = 0"),
            adv0,
            pallas::Base::zero(),
        )?;
        let is_native_asset = constant_cell(
            layouter.namespace(|| "is_native_asset = 0"),
            adv0,
            pallas::Base::zero(),
        )?;

        let g_d_old = NonIdentityPoint::new(
            ecc_chip.clone(),
            layouter.namespace(|| "g_d_old"),
            old.map(|n| n.g_d),
        )?;
        let pk_d_old = NonIdentityPoint::new(
            ecc_chip.clone(),
            layouter.namespace(|| "pk_d_old"),
            old.map(|n| n.pk_d),
        )?;
        let v_old = free_advice(
            layouter.namespace(|| "v_old"),
            adv0,
            old.map(|n| NoteValue::from_raw(n.value)),
        )?;
        let rho_old = free_advice(layouter.namespace(|| "rho_old"), adv0, old.map(|n| n.rho))?;
        let psi_old = free_advice(layouter.namespace(|| "psi_old"), adv0, old.map(|n| n.psi))?;
        let rcm_old = ScalarFixed::new(
            ecc_chip.clone(),
            layouter.namespace(|| "rcm_old"),
            old.map(|n| n.rcm),
        )?;

        // ---- N0 + N3: cm_old recomputed from the old note -------------------
        // The pinned ZSA NoteCommit gadget, `is_native_asset` fixed to 0: the
        // commitment is in the ZSA domain (Q_ZSA), with AssetBase in the
        // message. `cm_old` is this gadget's OUTPUT. It is never witnessed.
        let cm_old = note_commit(
            layouter.namespace(|| "cm_old = NoteCommit^ZSA(old note)"),
            SinsemillaChip::construct(config.sinsemilla_config_1.clone()),
            ecc_chip.clone(),
            NoteCommitChip::construct(config.old_note_commit_config.clone()),
            g_d_old.inner(),
            pk_d_old.inner(),
            v_old.clone(),
            rho_old.clone(),
            psi_old.clone(),
            rcm_old,
            Some(ZsaNoteCommitParams {
                cond_swap_chip: cond_swap_chip(),
                asset: asset.inner().clone(),
                // L1 renamed the flag. Polarity is unchanged: 0 selects Q_ZSA.
                is_zatoshi_asset: is_native_asset.clone(),
            }),
        )?;

        // ---- N9: Extract_P(cm_old) == public cmx_old -----------------------
        let cmx_old = cm_old.extract_p();
        layouter.constrain_instance(cmx_old.inner().cell(), config.primary, CMX_OLD)?;

        // ---- N4: DeriveNullifier(nk, rho_old, psi_old, SAME cm_old) == nf_old
        // The pinned ZSA nullifier gadget with split_flag fixed to 0. `&cm_old`
        // is the very Point N9 extracted from.
        let nf_old = derive_nullifier(
            layouter.namespace(|| "nf_old = DeriveNullifier_nk(rho_old, psi_old, cm_old)"),
            PoseidonChip::construct(config.poseidon_config.clone()),
            AddChip::construct(config.add_config.clone()),
            ecc_chip.clone(),
            rho_old,
            &psi_old,
            &cm_old,
            nk,
            Some(ZsaNullifierParams {
                cond_swap_chip: cond_swap_chip(),
                split_flag,
            }),
        )?;
        layouter.constrain_instance(nf_old.inner().cell(), config.primary, NF_OLD)?;

        // ---- N7 + N5 + N8: successor note --------------------------------
        let g_d_new = NonIdentityPoint::new(
            ecc_chip.clone(),
            layouter.namespace(|| "g_d_new"),
            new.map(|n| n.g_d),
        )?;
        let pk_d_new = NonIdentityPoint::new(
            ecc_chip.clone(),
            layouter.namespace(|| "pk_d_new"),
            new.map(|n| n.pk_d),
        )?;
        let v_new = free_advice(
            layouter.namespace(|| "v_new"),
            adv0,
            new.map(|n| NoteValue::from_raw(n.value)),
        )?;
        let rho_new = free_advice(layouter.namespace(|| "rho_new"), adv0, new.map(|n| n.rho))?;
        // N5: rho_new == nf_old (the derived nullifier cell, itself == public nf_old).
        layouter.assign_region(
            || "N5 rho_new == nf_old",
            |mut region| region.constrain_equal(rho_new.cell(), nf_old.inner().cell()),
        )?;
        let psi_new = free_advice(layouter.namespace(|| "psi_new"), adv0, new.map(|n| n.psi))?;
        let rcm_new = ScalarFixed::new(
            ecc_chip.clone(),
            layouter.namespace(|| "rcm_new"),
            new.map(|n| n.rcm),
        )?;

        let cm_new = note_commit(
            layouter.namespace(|| "cm_new = NoteCommit^ZSA(new note)"),
            SinsemillaChip::construct(config.sinsemilla_config_2.clone()),
            ecc_chip,
            NoteCommitChip::construct(config.new_note_commit_config.clone()),
            g_d_new.inner(),
            pk_d_new.inner(),
            v_new,
            rho_new,
            psi_new,
            rcm_new,
            Some(ZsaNoteCommitParams {
                cond_swap_chip: cond_swap_chip(),
                asset: asset.inner().clone(),
                is_zatoshi_asset: is_native_asset,
            }),
        )?;
        let cmx_new = cm_new.extract_p();
        layouter.constrain_instance(cmx_new.inner().cell(), config.primary, CMX_NEW)?;

        // ---- N1 + N10 gate --------------------------------------------------
        let [gx, gy, px, py] = reference_recipient_coords();
        let [nx, ny] = native_asset_coords();
        layouter.assign_region(
            || "NormalLineageStepV1: N1, N10",
            |mut region| {
                config.q_lineage.enable(&mut region, 0)?;
                let a = config.advices;

                v_old.copy_advice(|| "v_old", &mut region, a[0], 0)?;
                region.assign_advice(
                    || "inv v_old",
                    a[1],
                    0,
                    || old.map(|n| inv_or_zero(pallas::Base::from(n.value))),
                )?;

                let pairs = [
                    (g_d_old.inner().x(), gx, 2),
                    (g_d_old.inner().y(), gy, 4),
                    (pk_d_old.inner().x(), px, 6),
                    (pk_d_old.inner().y(), py, 8),
                ];
                for (cell, reference, col) in pairs {
                    cell.copy_advice(|| "coord", &mut region, a[col], 0)?;
                    region.assign_advice(
                        || "inv",
                        a[col + 1],
                        0,
                        || cell.value().map(|v| inv_or_zero(*v - reference)),
                    )?;
                }
                let d = |cell: &AssignedCell<pallas::Base, pallas::Base>, r: pallas::Base| {
                    cell.value().map(move |v| *v - r)
                };
                let factor =
                    |dv: Value<pallas::Base>| dv.map(|d| d * inv_or_zero(d) - pallas::Base::one());
                let t = factor(d(&g_d_old.inner().x(), gx)) * factor(d(&g_d_old.inner().y(), gy));

                let (ax, ay) = (asset.inner().x(), asset.inner().y());
                ax.copy_advice(|| "x(asset)", &mut region, a[0], 1)?;
                region.assign_advice(|| "inv", a[1], 1, || d(&ax, nx).map(inv_or_zero))?;
                ay.copy_advice(|| "y(asset)", &mut region, a[2], 1)?;
                region.assign_advice(|| "inv", a[3], 1, || d(&ay, ny).map(inv_or_zero))?;
                region.assign_advice(|| "t", a[4], 1, || t)?;
                Ok(())
            },
        )?;

        Ok(())
    }
}
