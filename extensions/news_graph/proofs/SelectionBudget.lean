import Lean

namespace Newsmap
-- IDs below stand for exact exported IDs and ordered endpoint references.
-- Unique IDs and a correctly parsed immutable snapshot are refinement assumptions.
structure Edge where
  id : Nat
  left : Nat
  right : Nat
  deriving Repr, DecidableEq

def select (xs : List Edge) (id left right : Nat) : Option Edge :=
  match xs with
  | [] => none
  | x :: rest => if x.id = id ∧ x.left = left ∧ x.right = right then some x
                 else select rest id left right

theorem exact_selection (xs : List Edge) (id left right : Nat) (e : Edge)
    (h : select xs id left right = some e) : e.id = id ∧ e.left = left ∧ e.right = right := by
  induction xs with
  | nil => simp [select] at h
  | cons x rest ih =>
    simp only [select] at h
    split at h
    · cases h; assumption
    · exact ih h

structure Budget where
  cap : Nat
  settled : Nat := 0
  held : Nat := 0
  frozen : Bool := false
  deriving Repr

def recordBill (s : Budget) (amount : Nat) : Budget :=
  {s with settled := s.settled + amount, held := 0,
          frozen := s.frozen || decide (amount > s.held)}

theorem bill_not_hidden (s : Budget) (n : Nat) :
    (recordBill s n).settled = s.settled + n := rfl

theorem overrun_freezes (s : Budget) (n : Nat) (h : s.held < n) :
    (recordBill s n).frozen = true := by simp [recordBill, h]

theorem bounded_bill_safe (s : Budget) (n : Nat)
    (h : s.settled + s.held ≤ s.cap) (adequateBound : n ≤ s.held) :
    (recordBill s n).settled + (recordBill s n).held ≤ s.cap := by
  simp [recordBill]; omega

#print axioms exact_selection
#print axioms bill_not_hidden
#print axioms overrun_freezes
#print axioms bounded_bill_safe

-- The same immutable JSON trace is consumed by the production Python regression.
#eval do
  let text ← IO.FS.readFile "extensions/news_graph/proofs/trace.json"
  let j ← IO.ofExcept (Lean.Json.parse text)
  let settled ← IO.ofExcept (j.getObjValAs? Nat "settled")
  let held ← IO.ofExcept (j.getObjValAs? Nat "held")
  let bill ← IO.ofExcept (j.getObjValAs? Nat "bill")
  let cap ← IO.ofExcept (j.getObjValAs? Nat "cap")
  let s : Budget := {cap := cap, settled := settled, held := held}
  IO.println (repr (recordBill s bill))
end Newsmap
