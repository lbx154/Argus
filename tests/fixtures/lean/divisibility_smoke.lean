import Mathlib

theorem dvd_linear_combination
    (a b c m n : Int) (hab : a ∣ b) (hac : a ∣ c) :
    a ∣ (m * b + n * c) := by
  rcases hab with ⟨kb, rfl⟩
  rcases hac with ⟨kc, rfl⟩
  refine ⟨m * kb + n * kc, ?_⟩
  ring
