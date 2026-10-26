import Lean

namespace NewsGraph

-- A finite identity model. Nat values stand for scoped immutable IDs/digests;
-- collision resistance, parsing and correspondence with stored bytes are assumptions.
structure ClaimRef where
  namespaceId : Nat
  item : Nat
  revision : Nat
  claim : Nat
  deriving DecidableEq, Repr

structure Binding where
  ref : ClaimRef
  payloadDigest : Nat
  anchorsDigest : Nat
  deriving DecidableEq, Repr

structure Span where
  start : Nat
  stop : Nat
  quote : List Char
  deriving DecidableEq, Repr

def exactSpan (text : List Char) (s : Span) : Bool :=
  decide (s.start < s.stop ∧ s.stop ≤ text.length ∧
          s.quote = (text.drop s.start).take (s.stop - s.start))

theorem exact_span_has_bounds_and_quote (text : List Char) (s : Span)
    (h : exactSpan text s = true) : s.start < s.stop ∧ s.stop ≤ text.length ∧
    s.quote = (text.drop s.start).take (s.stop - s.start) := by
  exact of_decide_eq_true h

inductive Review where
  | none | accept | reject | defer | revoke
  deriving DecidableEq, Repr

structure NewsState where
  retained : List Binding
  selectedLeft : Binding
  selectedRight : Binding
  boundLeft : Binding
  boundRight : Binding
  -- Producer resolution must explicitly establish all three gates.
  eligible : Bool := false
  anchorsValid : Bool := false
  premisesValid : Bool := false
  modelAssessed : Bool := false
  review : Review := .none
  token : Nat := 0
  history : List Review := []
  deriving Repr

def applicable (s : NewsState) : Bool :=
  s.eligible && s.anchorsValid && s.premisesValid &&
    decide (s.boundLeft ∈ s.retained ∧ s.boundRight ∈ s.retained) && decide (s.selectedLeft = s.boundLeft) &&
    decide (s.selectedRight = s.boundRight)
def humanActive (s : NewsState) : Bool := decide (s.review = .accept) && applicable s
def modelActive (s : NewsState) : Bool :=
  s.modelAssessed && decide (s.review = .none) && applicable s

-- Import here is AFTER bootstrap and keeps all bindings/eligibility already resolved.
-- Evidence repair of a previously missing binding is outside this import operation.
def retainRevision (s : NewsState) (b : Binding) : NewsState :=
  {s with retained := s.retained ++ [b]}
def selectLeft (s : NewsState) (b : Binding) : NewsState :=
  {s with selectedLeft := b, token := s.token + 1}
def selectRight (s : NewsState) (b : Binding) : NewsState :=
  {s with selectedRight := b, token := s.token + 1}
def revokePremise (s : NewsState) : NewsState :=
  {s with premisesValid := false, token := s.token + 1}
-- Core transition after command validation; submitReview below checks rationale.
-- Nat token abstracts changing event IDs, not the bytes of a cryptographic hash.
def reviewCommand (s : NewsState) (expected : Nat) (r : Review) : NewsState :=
  if expected = s.token ∧ r ≠ .none ∧ (r ≠ .accept ∨ applicable s = true) then
    {s with review := r, token := s.token + 1, history := s.history ++ [r]} else s

def submitReview (s : NewsState) (expected : Nat) (r : Review) (rationale : String) : NewsState :=
  if rationale.trim.isEmpty then s else reviewCommand s expected r

theorem blank_rationale_noop (s : NewsState) (expected : Nat) (r : Review) (rationale : String)
    (h : rationale.trim.isEmpty = true) : submitReview s expected r rationale = s := by
  simp [submitReview, h]

def historyConsistent (s : NewsState) : Prop :=
  s.history.reverse.head? = if s.review = .none then none else some s.review

theorem review_preserves_history_consistency (s : NewsState) (expected : Nat) (r : Review)
    (h : historyConsistent s) : historyConsistent (reviewCommand s expected r) := by
  simp only [reviewCommand]
  split
  · rename_i hg
    simp [historyConsistent, List.reverse_append, hg.2.1]
  · exact h

theorem changed_right_binding_inactive (s : NewsState) (b : Binding) (h : b ≠ s.boundRight) :
    humanActive (selectRight s b) = false ∧ modelActive (selectRight s b) = false := by
  simp [humanActive, modelActive, applicable, selectRight, h]

theorem unresolved_anchors_inactive (s : NewsState) (h : s.anchorsValid = false) :
    humanActive s = false ∧ modelActive s = false := by
  simp [humanActive, modelActive, applicable, h]

-- Immutable-key retention, not same-ref evidence repair/replacement. Both bound
-- bindings must already exist; without those hypotheses import can repair absence.
theorem import_does_not_transfer_approval (s : NewsState) (b : Binding)
    (hl : s.boundLeft ∈ s.retained) (hr : s.boundRight ∈ s.retained) :
    humanActive (retainRevision s b) = humanActive s := by
  have hl2 := List.mem_append_of_mem_left [b] hl
  have hr2 := List.mem_append_of_mem_left [b] hr
  simp [humanActive, applicable, retainRevision, hl, hr, hl2, hr2]

theorem active_requires_retained_bindings (s : NewsState) (h : humanActive s = true) :
    s.boundLeft ∈ s.retained ∧ s.boundRight ∈ s.retained := by
  simp [humanActive, applicable, Bool.and_eq_true] at h
  exact h.2.1.1.2

theorem changed_binding_inactive (s : NewsState) (b : Binding) (h : b ≠ s.boundLeft) :
    humanActive (selectLeft s b) = false ∧ modelActive (selectLeft s b) = false := by
  simp [humanActive, modelActive, applicable, selectLeft, h]

theorem revoked_premise_inactive (s : NewsState) :
    humanActive (revokePremise s) = false ∧ modelActive (revokePremise s) = false := by
  simp [humanActive, modelActive, applicable, revokePremise]

theorem stale_review_noop (s : NewsState) (old : Nat) (r : Review) (h : old ≠ s.token) :
    reviewCommand s old r = s := by simp [reviewCommand, h]

theorem failed_acceptance_noop (s : NewsState) (h : applicable s = false) :
    reviewCommand s s.token .accept = s := by simp [reviewCommand, h]

theorem selection_retains_history (s : NewsState) (b : Binding) :
    (selectLeft s b).history = s.history := rfl

theorem human_and_model_labels_distinct (s : NewsState) (h : humanActive s = true) :
    modelActive s = false := by
  cases hr : s.review <;> simp [humanActive, modelActive, hr] at *

theorem human_rejection_blocks_model (s : NewsState) (h : s.review = .reject) :
    modelActive s = false := by simp [modelActive, h]

-- Activity is restored; the head-selection event (and thus freshness token) is not.
theorem same_binding_reselection_restores (s : NewsState) (other : Binding) :
    humanActive (selectLeft (selectLeft s other) s.selectedLeft) = humanActive s := rfl

theorem reselection_does_not_restore_token (s : NewsState) (other : Binding) :
    (selectLeft (selectLeft s other) s.selectedLeft).token ≠ s.token := by
  simp [selectLeft]; omega

theorem review_history_not_erased (s : NewsState) (expected : Nat) (r old : Review)
    (h : old ∈ s.history) : old ∈ (reviewCommand s expected r).history := by
  simp only [reviewCommand]
  split
  · exact List.mem_append_of_mem_left [r] h
  · exact h

-- Re-running assessment does not mutate an existing proposal's provenance.
-- A genuinely new proposal has its own state/ID; this operation is reuse of this one.
def rerunModel (s : NewsState) : NewsState := s
theorem rerun_preserves_human_rejection (s : NewsState) (h : s.review = .reject) :
    modelActive (rerunModel s) = false := by simp [modelActive, rerunModel, h]

inductive Relation where
  | supports | contradicts | corrects | supersedes | duplicates | elaborates
  | structural | identity
  deriving DecidableEq, Repr
inductive Mode where
  | documents | claims
  deriving DecidableEq, Repr

structure DisplayEdge where
  id : Nat
  source : Nat
  target : Nat
  kind : Relation
  decision : Nat
  context : String
  deriving DecidableEq, Repr
structure Selection where
  id : Nat
  source : Nat
  target : Nat
  mode : Mode
  deriving Repr

-- Small standard capPrefix operation, equivalent to JS slice(0, cap).
def capPrefix : Nat → List α → List α
  | 0, _ => []
  | _ + 1, [] => []
  | n + 1, x :: xs => x :: capPrefix n xs

theorem prefix_length (n : Nat) (xs : List α) : (capPrefix n xs).length ≤ n := by
  induction n generalizing xs with
  | zero => simp [capPrefix]
  | succ n ih =>
    cases xs with
    | nil => simp [capPrefix]
    | cons x xs => simpa [capPrefix] using Nat.succ_le_succ (ih xs)

theorem prefix_member (n : Nat) (xs : List α) (x : α) (h : x ∈ capPrefix n xs) : x ∈ xs := by
  induction n generalizing xs with
  | zero => simp [capPrefix] at h
  | succ n ih =>
    cases xs with
    | nil => simp [capPrefix] at h
    | cons y ys =>
      simp only [capPrefix, List.mem_cons] at h ⊢
      exact h.elim Or.inl (fun hm => Or.inr (ih ys hm))

def closedOn (nodes : List Nat) (e : DisplayEdge) : Bool :=
  decide (e.source ∈ nodes ∧ e.target ∈ nodes)
def displayed (nodeCap edgeCap : Nat) (nodes : List Nat) (edges : List DisplayEdge)
    (keep : DisplayEdge → Bool) : List DisplayEdge :=
  capPrefix edgeCap (edges.filter (fun e => keep e && closedOn (capPrefix nodeCap nodes) e))

theorem displayed_edge_bound (nc ec : Nat) (ns : List Nat) (es : List DisplayEdge)
    (keep : DisplayEdge → Bool) : (displayed nc ec ns es keep).length ≤ ec :=
  prefix_length _ _

theorem displayed_canonical_and_closed (nc ec : Nat) (ns : List Nat) (es : List DisplayEdge)
    (keep : DisplayEdge → Bool) (e : DisplayEdge) (h : e ∈ displayed nc ec ns es keep) :
    e ∈ es ∧ keep e = true ∧ e.source ∈ capPrefix nc ns ∧ e.target ∈ capPrefix nc ns := by
  have hf := prefix_member ec _ e h
  have hm := List.mem_filter.mp hf
  have hc : closedOn (capPrefix nc ns) e = true := by
    cases hkeep : keep e <;> simp_all
  have hk : keep e = true := by cases hkeep : keep e <;> simp_all
  exact ⟨hm.1, hk, of_decide_eq_true hc⟩

-- Pagination is a window after filtering to the displayed node set.
def skipPrefix : Nat → List α → List α
  | 0, xs => xs
  | _ + 1, [] => []
  | n + 1, _ :: xs => skipPrefix n xs

theorem skipped_member (n : Nat) (xs : List α) (x : α) (h : x ∈ skipPrefix n xs) : x ∈ xs := by
  induction n generalizing xs with
  | zero => exact h
  | succ n ih =>
    cases xs with
    | nil => simp [skipPrefix] at h
    | cons y ys => exact List.mem_cons_of_mem y (ih ys h)

def displayedPage (nc ec page : Nat) (ns : List Nat) (es : List DisplayEdge)
    (keep : DisplayEdge → Bool) : List DisplayEdge :=
  capPrefix ec (skipPrefix (page * ec) (es.filter (fun e => keep e && closedOn (capPrefix nc ns) e)))

theorem page_edge_bound (nc ec page : Nat) (ns : List Nat) (es : List DisplayEdge)
    (keep : DisplayEdge → Bool) : (displayedPage nc ec page ns es keep).length ≤ ec := prefix_length _ _

theorem page_canonical_and_closed (nc ec page : Nat) (ns : List Nat) (es : List DisplayEdge)
    (keep : DisplayEdge → Bool) (e : DisplayEdge) (h : e ∈ displayedPage nc ec page ns es keep) :
    e ∈ es ∧ keep e = true ∧ e.source ∈ capPrefix nc ns ∧ e.target ∈ capPrefix nc ns := by
  have hf := skipped_member (page * ec) _ e (prefix_member ec _ e h)
  have hm := List.mem_filter.mp hf
  have hc : closedOn (capPrefix nc ns) e = true := by
    cases hkeep : keep e <;> simp_all
  have hk : keep e = true := by cases hkeep : keep e <;> simp_all
  exact ⟨hm.1, hk, of_decide_eq_true hc⟩

-- Caller supplies the eligible current-scope edges, including authorized history.
-- Selection deliberately preserves the entire canonical edge, not just its pair.
def resolveEdge (edges : List DisplayEdge) (s : Selection) : Option DisplayEdge :=
  match edges with
  | [] => none
  | e :: rest => if e.id = s.id ∧ e.source = s.source ∧ e.target = s.target then some e
                 else resolveEdge rest s

def comparison (edges : List DisplayEdge) (selection : Option Selection) (mode : Mode) : Option DisplayEdge :=
  match selection with
  | none => none
  | some s => if s.mode = mode then resolveEdge edges s else none

theorem resolved_exact (es : List DisplayEdge) (s : Selection) (e : DisplayEdge)
    (h : resolveEdge es s = some e) : e ∈ es ∧ e.id = s.id ∧ e.source = s.source ∧ e.target = s.target := by
  induction es with
  | nil => simp [resolveEdge] at h
  | cons x xs ih =>
    simp only [resolveEdge] at h
    split at h
    · cases h; exact ⟨by simp, by assumption⟩
    · obtain ⟨hm, hid⟩ := ih h
      exact ⟨by simp [hm], hid⟩

-- Uniqueness is a DTO construction invariant, separate from lookup correctness.
theorem resolved_unique_context (es : List DisplayEdge) (s : Selection) (e candidate : DisplayEdge)
    (unique : ∀ x ∈ es, ∀ y ∈ es, x.id = y.id → x = y)
    (h : resolveEdge es s = some e) (hc : candidate ∈ es) (hid : candidate.id = s.id) :
    e = candidate := by
  have he := resolved_exact es s e h
  exact unique e he.1 candidate hc (he.2.1.trans hid.symm)

theorem neutral_pair_has_no_relation (es : List DisplayEdge) (m : Mode) :
    comparison es none m = none := rfl

theorem changed_mode_clears_relation (es : List DisplayEdge) (s : Selection) (m : Mode)
    (h : s.mode ≠ m) : comparison es (some s) m = none := by simp [comparison, h]

-- Public history: grant filtering only. No private head/revocation lookup.
-- Closure/authorized evidence are validated before constructing these records.
structure HistoricalDecision where
  id : Nat
  review : Review
  dependencies : List Nat := []
  evidenceAuthorized : Bool := true
  deriving DecidableEq, Repr

def publicHistory (allowed : List Nat) (log : List HistoricalDecision) : List HistoricalDecision :=
  log.filter (fun e => decide (e.id ∈ allowed))

theorem hidden_append_invariant (allowed : List Nat) (log : List HistoricalDecision)
    (hidden : HistoricalDecision) (h : hidden.id ∉ allowed) :
    publicHistory allowed (log ++ [hidden]) = publicHistory allowed log := by
  induction log with
  | nil => simp [publicHistory, h]
  | cons x xs ih =>
    simp only [List.cons_append, publicHistory, List.filter_cons]
    split <;> simp_all [publicHistory]

structure PublicSnapshot where
  decisions : List HistoricalDecision
  currentApplicability : Option Bool := none
  deriving DecidableEq, Repr

def closedHistory (allowed : List Nat) (history : List HistoricalDecision) : Bool :=
  let ids := history.map HistoricalDecision.id
  allowed.all ids.contains && history.all (fun e => e.evidenceAuthorized && e.dependencies.all ids.contains)

def publicSnapshot (allowed : List Nat) (log : List HistoricalDecision) : Option PublicSnapshot :=
  let history := publicHistory allowed log
  if closedHistory allowed history then some {decisions := history} else none

theorem hidden_append_preserves_success_or_failure (allowed : List Nat) (log : List HistoricalDecision)
    (hidden : HistoricalDecision) (h : hidden.id ∉ allowed) :
    publicSnapshot allowed (log ++ [hidden]) = publicSnapshot allowed log := by
  simp [publicSnapshot, hidden_append_invariant allowed log hidden h]

theorem public_current_not_disclosed (allowed : List Nat) (log : List HistoricalDecision)
    (result : PublicSnapshot) (h : publicSnapshot allowed log = some result) :
    result.currentApplicability = none := by
  simp only [publicSnapshot] at h
  split at h
  · cases h; rfl
  · simp at h

theorem unclosed_history_not_exported (allowed : List Nat) (log : List HistoricalDecision)
    (h : closedHistory allowed (publicHistory allowed log) = false) :
    publicSnapshot allowed log = none := by simp [publicSnapshot, h]

-- Stronger than the name: fresh revoke is allowed for both applicable and inapplicable states.
theorem fresh_revoke_allowed_when_inapplicable (s : NewsState) :
    (reviewCommand s s.token .revoke).review = .revoke := by simp [reviewCommand]

#print axioms blank_rationale_noop
#print axioms review_preserves_history_consistency
#print axioms changed_right_binding_inactive
#print axioms unresolved_anchors_inactive
#print axioms reselection_does_not_restore_token
#print axioms resolved_unique_context
#print axioms exact_span_has_bounds_and_quote
#print axioms import_does_not_transfer_approval
#print axioms active_requires_retained_bindings
#print axioms changed_binding_inactive
#print axioms revoked_premise_inactive
#print axioms stale_review_noop
#print axioms failed_acceptance_noop
#print axioms selection_retains_history
#print axioms human_and_model_labels_distinct
#print axioms human_rejection_blocks_model
#print axioms same_binding_reselection_restores
#print axioms review_history_not_erased
#print axioms rerun_preserves_human_rejection
#print axioms prefix_length
#print axioms prefix_member
#print axioms displayed_edge_bound
#print axioms displayed_canonical_and_closed
#print axioms resolved_exact
#print axioms neutral_pair_has_no_relation
#print axioms changed_mode_clears_relation
#print axioms hidden_append_invariant
#print axioms skipped_member
#print axioms page_edge_bound
#print axioms page_canonical_and_closed
#print axioms hidden_append_preserves_success_or_failure
#print axioms public_current_not_disclosed
#print axioms unclosed_history_not_exported
#print axioms fresh_revoke_allowed_when_inapplicable

-- Executable conformance examples, NOT additional universal proofs.
def fixtureBinding (revision : Nat) (item : Nat := 1) : Binding :=
  {ref := {namespaceId := 1, item := item, revision := revision, claim := 1},
   payloadDigest := revision * 10, anchorsDigest := revision * 100}
def initialState : NewsState :=
  let l := fixtureBinding 1
  let r := fixtureBinding 1 2
  {retained := [l,r], selectedLeft := l, selectedRight := r, boundLeft := l, boundRight := r,
   eligible := true, anchorsValid := true, premisesValid := true, modelAssessed := true}
def fixtureStep (s : NewsState) (action : String) (expected : Nat) : NewsState :=
  match action with
  | "accept" | "ineligible_accept" => submitReview s expected .accept "Trace command"
  | "reject" | "stale_reject" => submitReview s expected .reject "Trace command"
  | "import_new" => retainRevision s (fixtureBinding 2)
  | "select_new" => selectLeft s (fixtureBinding 2)
  | "select_original" => selectLeft s (fixtureBinding 1)
  | "rerun_model" => rerunModel s
  | "revoke_premise" => revokePremise s
  | _ => s

#eval do
  let text ← IO.FS.readFile "extensions/news_graph/proofs/news-trace.json"
  let json ← IO.ofExcept (Lean.Json.parse text)
  let rows ← IO.ofExcept (json.getObjValAs? (Array Lean.Json) "review")
  let mut state := initialState
  for row in rows do
    let action ← IO.ofExcept (row.getObjValAs? String "action")
    let expected ← IO.ofExcept (row.getObjValAs? Nat "expected_token")
    state := fixtureStep state action expected
    let human ← IO.ofExcept (row.getObjValAs? Bool "human")
    let model ← IO.ofExcept (row.getObjValAs? Bool "model")
    let history ← IO.ofExcept (row.getObjValAs? Nat "history")
    let token ← IO.ofExcept (row.getObjValAs? Nat "token")
    if humanActive state != human || modelActive state != model || state.history.length != history || state.token != token then
      throw (IO.userError ("review trace mismatch: " ++ action))
  let graph ← IO.ofExcept (json.getObjVal? "graph")
  let nodes ← IO.ofExcept (graph.getObjValAs? (List Nat) "nodes")
  let nc ← IO.ofExcept (graph.getObjValAs? Nat "node_cap")
  let ec ← IO.ofExcept (graph.getObjValAs? Nat "edge_cap")
  let inputEdges ← IO.ofExcept (graph.getObjValAs? (Array Lean.Json) "edges")
  let mut edges : List DisplayEdge := []
  for e in inputEdges do
    let id ← IO.ofExcept (e.getObjValAs? Nat "id")
    let src ← IO.ofExcept (e.getObjValAs? Nat "source")
    let dst ← IO.ofExcept (e.getObjValAs? Nat "target")
    let decision ← IO.ofExcept (e.getObjValAs? Nat "decision")
    let context ← IO.ofExcept (e.getObjValAs? String "context")
    edges := edges ++ [({id := id, source := src, target := dst, kind := .supports, decision := decision, context := context} : DisplayEdge)]
  let expectedIds ← IO.ofExcept (graph.getObjValAs? (List Nat) "expected_ids")
  if (displayed nc ec nodes edges (fun _ => true)).map DisplayEdge.id != expectedIds then
    throw (IO.userError "graph closure/cap trace mismatch")
  let expectedPage ← IO.ofExcept (graph.getObjValAs? (List Nat) "page_one_ids")
  if (displayedPage nc ec 1 nodes edges (fun _ => true)).map DisplayEdge.id != expectedPage then
    throw (IO.userError "page-one trace mismatch")
  let selected ← IO.ofExcept (graph.getObjValAs? Nat "selected")
  let ctx ← IO.ofExcept (graph.getObjValAs? String "selected_context")
  let picked := comparison (displayed nc ec nodes edges (fun _ => true))
    (some {id := selected, source := 2, target := 1, mode := .claims}) .claims
  if picked.map DisplayEdge.context != some ctx then throw (IO.userError "selected context mismatch")
  let uni ← IO.ofExcept (json.getObjVal? "unicode")
  let source ← IO.ofExcept (uni.getObjValAs? String "text")
  let start ← IO.ofExcept (uni.getObjValAs? Nat "start")
  let stop ← IO.ofExcept (uni.getObjValAs? Nat "stop")
  let quote ← IO.ofExcept (uni.getObjValAs? String "quote")
  if !exactSpan source.toList {start := start, stop := stop, quote := quote.toList} then
    throw (IO.userError "Unicode scalar span mismatch")
  IO.println "NEWS_TRACE_PASS: review, revision, stale command, Unicode, closure, caps, exact comparison"

end NewsGraph
