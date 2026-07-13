import Mathlib

theorem erdos_straus_even_local_identity (m : ℕ) (hm : 0 < m) :
    (4 : ℚ) / (2 * m) =
      1 / m + 1 / (2 * m) + 1 / (2 * m) := by
  have hm0 : (m : ℚ) ≠ 0 := by
    exact_mod_cast Nat.ne_of_gt hm
  field_simp [hm0]
  ring
