# L35 theorem and proof — verbatim frozen excerpt

> Source: `research/SOLVE.md` §15, captured 2026-07-16 UTC.
> Scope: a terminal method-boundary theorem for the multiplier-coupled C7 integer relaxation; not a solution of the open A*/F4 target, and not a revision of the external disproof of original #707.

## 15. The multiplier-coupled \(C_7\) quotient

This section starts a new solve increment.  It uses the same zero-sum
normalizing translate as Theorem 6.1 and combines its multiplication-by-\(q\)
invariance with all seven Hall quotient autocorrelations from Theorem 14.1.
The first proposition classifies the generic weighted vectors.  The
prescribed-set lower bounds and their all-translate verdict are intentionally
left to the next increment.

### Proposition 15.1 (exact orbit-reduced \(C_7\) parameterization)

Let \(q\ge2\), \(v=q^2+q+1\), and assume \(7\mid v\), equivalently
\(q\equiv2\) or \(4\pmod7\).  Put \(s=v/7\).  Suppose
\(\widetilde D\subseteq C_v\) is a zero-sum normalized cyclic
\((v,q+1,1)\) difference set, and let
\[
 n_j=|\widetilde D\cap\pi^{-1}(j)|\qquad(j\in C_7)
 \tag{15.1}
\]
for reduction \(\pi:C_v\twoheadrightarrow C_7\).  Then multiplication by
\(q\) fixes \(\widetilde D\), and its orbits on \(C_7\) are
\[
 \{0\},\qquad H=\{1,2,4\},\qquad -H=\{3,5,6\}.
 \tag{15.2}
\]
Consequently there are nonnegative integers \(x,y,z\), each at most \(s\),
such that, in coordinates \(0,1,\ldots,6\),
\[
 (n_0,\ldots,n_6)=(x,y,y,z,y,z,z).                                    \tag{15.3}
\]

More generally, a vector of the form (15.3) satisfies the cardinality equation
and all seven autocorrelation equations (14.1)--(14.2) if and only if there
are integers \(a,d\) such that
\[
 a^2+7d^2=4q,\qquad a\equiv-2(q+1)\pmod7,                              \tag{15.4}
\]
and
\[
 \boxed{
 x=\frac{q+1-3a}{7},\qquad
 y=\frac{2(q+1)+a+7d}{14},\qquad
 z=\frac{2(q+1)+a-7d}{14}.}                                           \tag{15.5}
\]
For the nonnegative capacity-constrained feasibility problem one adds exactly
\(0\le x,y,z\le s\); no other equation is missing.  If
\(q\ge37\) and \(q\equiv2\) or \(4\pmod7\), every integral pair satisfying
(15.4) automatically makes (15.5) positive and at most \(s\).  Hence in this
range generic multiplier-coupled \(C_7\) feasibility is equivalent simply to
the binary quadratic representation (15.4).

#### Proof

Theorem 6.1 fixes \(\widetilde D\) under every element of \(H_q\).  In
particular it is fixed by their product \(q\).  The congruence
\(q^2+q+1\equiv0\pmod7\) has precisely the two roots \(2,4\), and either root
generates the order-three subgroup \(H=\{1,2,4\}\) of
\(C_7^\times\).  Thus multiplication by \(q\) gives (15.2), proving (15.3).

Write \(k=q+1\).  For a vector (15.3), multiplication by \(q\) gives
\(R(qt)=R(t)\), while the definition of periodic autocorrelation gives
\(R(-t)=R(t)\).  The two nonzero multiplication orbits \(H\) and \(-H\)
are exchanged by negation.  Hence all six nonzero autocorrelations are equal.
It follows that the complete Hall system is equivalent to
\[
 x+3y+3z=k,
 \qquad x^2+3y^2+3z^2=q+s:                                            \tag{15.6}
\]
indeed, if the common nonzero value is \(T\), then summing all seven
correlations gives
\[
 k^2=R(0)+6T=(q+s)+6T=q+7s,
\]
so \(T=s\).  This also proves that no nonzero shift has been discarded.

Center the three orbit values by
\[
 u=7x-k,\qquad p=7y-k,\qquad r=7z-k.                                  \tag{15.7}
\]
Equations (15.6) become
\[
 u+3p+3r=0,
 \qquad u^2+3p^2+3r^2=42q.                                           \tag{15.8}
\]
The second identity follows directly from
\[
 49(q+s)-14k^2+7k^2=42q.
\]
Eliminating \(u=-3(p+r)\) gives
\[
 2p^2+3pr+2r^2=7q.                                                     \tag{15.9}
\]
Moreover \(u,p,r\equiv-k\pmod7\).  Therefore
\[
 a:=p+r\equiv-2k\pmod7,
 \qquad d:=\frac{p-r}{7}\in\mathbb Z.
\]
Multiplying (15.9) by four and using
\[
 4(2p^2+3pr+2r^2)=7(p+r)^2+(p-r)^2
\]
yields (15.4); solving back for \(x,y,z\) yields (15.5).

Conversely, suppose (15.4) holds.  Reduction modulo four shows that \(a\)
and \(d\) have the same parity, so
\[
 p=(a+7d)/2,\qquad r=(a-7d)/2,
 \qquad u=-3a
\]
are integers.  The congruence in (15.4) gives
\(p,r,u\equiv-k\pmod7\), so (15.5) is integral.  Reversing the displayed
identities proves (15.8), (15.6), and hence every Hall autocorrelation.
This proves the iff, including the stated nonnegativity and capacity
conditions.

Finally, (15.4) gives \(|a|\le2\sqrt q\), whence
\[
 x\ge\frac{q+1-6\sqrt q}{7}.                                         \tag{15.10}
\]
Completing a square in (15.9) gives
\[
 7q=2\left(r+\frac{3p}{4}\right)^2+\frac{7p^2}{8},
\]
and symmetrically, so \(|p|,|r|\le2\sqrt{2q}\).  Therefore
\[
 y,z\ge\frac{q+1-2\sqrt{2q}}7.                                      \tag{15.11}
\]
For every admissible \(q\ge37\), both lower bounds are strictly positive.
Also \(s\ge q+1\) for \(q\ge7\), so positivity and
\(x+3y+3z=q+1\) imply \(x,y,z\le q+1\le s\).  Thus all sign and capacity
conditions are automatic in the asserted range. \(\square\)

This proposition is only a classification of the generic weighted quotient
system.  Hall's full quotient equations are classical (see `REVIEW.md` §9.5),
and the exact orbit-reduced parameterization (15.4)--(15.5) has not yet
received a novelty audit.  A feasible triple does not lift to a PDS.  In
particular, Proposition 15.1 alone neither excludes nor embeds \(A_*\); the
same-translate prescribed lower bounds must still be imposed.

### Theorem 15.2 (exact full-group lower bounds for every translate)

Retain the hypotheses and notation of Proposition 15.1, and put
\(A_*=\{0,1,3,11\}\).  For every \(c\in C_v\), define the actual point-set
orbit closure
\[
 U_c:=\bigcup_{a\in A_*}\{q^e(a+c):e=0,1,2\}\subseteq C_v,
 \qquad b_j(c):=|U_c\cap\pi^{-1}(j)|.                                  \tag{15.12}
\]
Thus repeated points are counted once.  If \(A_*\) embeds in a PDS whose
zero-sum normalizing translate is \(c\), then Theorem 6.1 gives
\(U_c\subseteq\widetilde D\), and hence
\[
 n_j\ge b_j(c)\qquad(j\in C_7).                                       \tag{15.13}
\]

The vector \(b(c)\) has the exact orbit form
\[
 b(c)=(\epsilon(c),\beta(c),\beta(c),\gamma(c),
                    \beta(c),\gamma(c),\gamma(c)).                    \tag{15.14}
\]
To compute its three entries without losing full-group collisions, let
\(\bar c=c\bmod7\), and use the following table.  The entries list the
*integer labels* \(a\in A_*\), not merely their quotient residues.

\[
\begin{array}{c|c|c|c}
\bar c&S_0(\bar c)&S_H(\bar c)&S_{-H}(\bar c)\\ \hline
0&\{0\}&\{1,11\}&\{3\}\\
1&\varnothing&\{0,1,3\}&\{11\}\\
2&\varnothing&\{0\}&\{1,3,11\}\\
3&\{11\}&\{1\}&\{0,3\}\\
4&\{3\}&\{0,11\}&\{1\}\\
5&\varnothing&\{3,11\}&\{0,1\}\\
6&\{1\}&\{3\}&\{0,11\}
\end{array}                                                           \tag{15.15}
\]
Here \(S_T(\bar c)=\{a\in A_*:a+c\bmod7\in T\}\).  On either
\(S_H\) or \(S_{-H}\), declare
\[
 a\sim_c b\quad\Longleftrightarrow\quad
 \{q^e(a+c):0\le e<3\}=\{q^e(b+c):0\le e<3\}\quad\text{in }C_v.
 \tag{15.16}
\]
Then
\[
 \beta(c)=|S_H(\bar c)/{\sim_c}|,
 \qquad
 \gamma(c)=|S_{-H}(\bar c)/{\sim_c}|.                                \tag{15.17}
\]
If \(S_0(\bar c)=\varnothing\), then \(\epsilon(c)=0\).  Otherwise
\(S_0(\bar c)=\{a_0\}\), and
\[
 \epsilon(c)=
 \begin{cases}
 1,&a_0+c\in\operatorname{Fix}_{C_v}(q),\\
 3,&a_0+c\notin\operatorname{Fix}_{C_v}(q).
 \end{cases}                                                          \tag{15.18}
\]
In particular, for every \(q\) and every full lift \(c\),
\[
 0\le\epsilon(c),\beta(c),\gamma(c)\le3.                             \tag{15.19}
\]

The dependence on the full residue \(c\bmod v\), which (15.15) alone cannot
see, is completely classified as follows.  For distinct \(a,b\in A_*\),
\(a\sim_c b\) if and only if at least one of
\[
 (q-1)c\equiv b-qa\pmod v,
 \qquad
 (q-1)c\equiv a-qb\pmod v                                             \tag{15.20}
\]
holds.  The quotient residues on which these two oriented collisions can
occur are:
\[
\begin{array}{c|cc|cc}
\{a,b\}&\multicolumn{2}{c|}{q\equiv2\pmod7}
       &\multicolumn{2}{c}{q\equiv4\pmod7}\\
 &q(a+c)=b+c&q(b+c)=a+c&q(a+c)=b+c&q(b+c)=a+c\\ \hline
\{0,1\}&1&5&5&1\\
\{0,3\}&3&1&1&3\\
\{0,11\}&4&6&6&4\\
\{1,3\}&1&2&2&1\\
\{1,11\}&2&0&0&2\\
\{3,11\}&5&2&2&5
\end{array}                                                           \tag{15.21}
\]
Every entry is a value of \(\bar c\).  Let
\(g=\gcd(q-1,v)=\gcd(q-1,3)\).  If \(g=1\), each oriented congruence in
(15.20) has exactly one full lift \(c\bmod v\).  If \(g=3\), it has three
lifts exactly when \(a\equiv b\pmod3\); among pairs from \(A_*\), this occurs
only for \(\{a,b\}=\{0,3\}\).  The two orientations never share a solution.
Finally,
\[
 \operatorname{Fix}_{C_v}(q)=
 \begin{cases}
 \{0\},&q\not\equiv1\pmod3,\\
 \{0,v/3,2v/3\},&q\equiv1\pmod3,
 \end{cases}                                                          \tag{15.22}
\]
and all these fixed points lie over the zero class modulo \(7\).
Equations (15.15)--(15.22) therefore classify the exact lower bound for every
\(c\in C_v\), rather than assigning a potentially false bound using only
\(c\bmod7\).

#### Proof

The four prescribed points are distinct modulo \(v\): this is immediate for
\(v=7\), and for the remaining admissible parameters \(v\ge21\), while their
integer labels are distinct and lie in an interval of length \(11<v\).  Since
\(q^3\equiv1\pmod v\), every set in the union (15.12) is a full \(q\)-orbit.
Modulo \(7\), a nonzero orbit is either \(H\) or \(-H\), with one point in
each of its three fibers; an orbit over zero stays entirely in the zero fiber.
The four shifted labels give (15.15) by direct reduction.  Different seed
labels in the same quotient orbit may nevertheless generate the same
*full-group* orbit, which is why counting their equivalence classes gives
(15.17), not merely \(|S_H|\) and \(|S_{-H}|\).

There is at most one seed over zero in every row of (15.15).  Its orbit has
size one precisely when it is fixed by \(q\), and otherwise has size three,
proving (15.18).  This proves (15.14)--(15.19), including all point collisions.

For distinct seeds, equality of their orbits means either
\(b+c=q(a+c)\) or \(a+c=q(b+c)\): the remaining nontrivial power \(q^2\)
is the reverse of one of these after multiplying by \(q\).  Rearrangement
proves (15.20).  Reducing those congruences modulo \(7\), where \(q-1\) is a
unit, gives (15.21).  The standard linear-congruence criterion says that each
orientation has either zero or \(g=\gcd(q-1,v)\) solutions, and
\(g=\gcd(q-1,3)\).  When \(g=3\), its right side is divisible by three exactly
when \(b-a\) is, because \(q\equiv1\pmod3\); inspection of \(A_*\) leaves only
\(\{0,3\}\).  A common solution to both orientations would make a nonfixed
three-cycle have period two, forcing its two distinct seeds to agree, so the
solution sets are disjoint.  Formula (15.22) is Corollary 7.7's fixed-subgroup
calculation.  Since \(7\mid v\), its two nonzero elements when present are
also divisible by seven.  This completes the full-lift classification.
\(\square\)

For later reference, when \(q\ge12\), no three prescribed seeds can lie in one
\(q\)-orbit.  Otherwise, after cyclically naming them \(a,b,d\), one has
\(d-b\equiv q(b-a)\pmod v\).  Since
\[
 |q(b-a)-(d-b)|\le11q+11<v=q^2+q+1
\]
for \(q\ge12\), the congruence forces the integer equality
\(d-b=q(b-a)\).  This is impossible because
\(|d-b|\le11<q\le|q(b-a)|\).  Thus in a three-label cell
of (15.15), (15.17) is either three (no collision) or two (one collision).
This simplification is not needed for the uniform bound (15.19), but confirms
that (15.20) does not hide a higher collision pattern in the unbounded range.

### Theorem 15.3 (terminal all-translate method boundary on an explicit infinite family)

For every integer \(r\ge0\), put
\[
 t=14r+9,\qquad q=t^2,
 \qquad v=q^2+q+1.                                                     \tag{15.23}
\]
Then \(q\equiv4\pmod7\), so \(7\mid v\).  The single multiplier-invariant
fiber vector
\[
 (n_0,\ldots,n_6)=(x,y,y,z,y,z,z),                                   \tag{15.24}
\]
where
\[
 x=\frac{t^2-6t+1}{7},
 \qquad y=z=\frac{t^2+t+1}{7},                                       \tag{15.25}
\]
is a nonnegative integral solution of the cardinality equation and all seven
Hall autocorrelation equations.  Moreover, for **every** full lift
\(c\in C_v\), not merely every class \(c\bmod7\), it satisfies
\[
 x\ge\epsilon(c),\qquad y\ge\beta(c),\qquad z\ge\gamma(c).            \tag{15.26}
\]
Thus the complete \(C_7\) relaxation that simultaneously imposes Hall's full
quotient autocorrelation, multiplication-by-\(q\) invariance, and the exact
same-translate orbit-closure lower bound for \(A_*+c\) is feasible for every
candidate normalizing translate \(c\), for every order in the explicit
unbounded family (15.23).

Consequently this multiplier-coupled \(C_7\) relaxation cannot prove an
\(A_*\)-specific non-embedding theorem on all sufficiently large admissible
orders, or even on all odd square orders in (15.23).  This is a terminal method
boundary, not a construction of a PDS or an embedding of \(A_*\).

#### Proof

Because \(t\equiv2\pmod7\), one has \(q=t^2\equiv4\pmod7\).  Set
\[
 a=2t,\qquad d=0.
\]
Then
\[
 a^2+7d^2=4t^2=4q,
 \qquad
 a+2(q+1)=2(t^2+t+1)\equiv0\pmod7.                                  \tag{15.27}
\]
Thus (15.4) holds, and substitution in (15.5) is exactly (15.25).
Proposition 15.1 now proves integrality and every Hall autocorrelation directly;
no existence theorem for primes or represented integers is being assumed.

At \(t=9\), the coordinates are \((x,y,z)=(4,13,13)\).  For \(t\ge9\),
\[
 (t+14)^2-6(t+14)+1-(t^2-6t+1)=28t+112>0,
\]
and
\[
 (t+14)^2+(t+14)+1-(t^2+t+1)=28t+210>0.
\]
Hence along \(t=14r+9\), all three coordinates are at least \(4\), and in
particular at least \(3\).  Theorem 15.2 gives, for every full residue
\(c\bmod v\),
\[
 0\le\epsilon(c),\beta(c),\gamma(c)\le3.
\]
This proves (15.26).  Since the same vector (15.24) works for every \(c\), the
claim is stronger than separate existence of a vector after choosing a
translate.

Finally, every \(t=14r+9\) is odd, so every \(q=t^2\) in this family is odd.
The family is strictly increasing and unbounded.  Therefore its feasibility is
not explained away by the independent even-order exclusion in Theorem 12.1,
and finite enumeration plays no role in the conclusion. \(\square\)

### Terminal classification and limitations

Theorems 15.2--15.3 select the third terminal branch of the quotient method.
They do not assert that every generic representation (15.4) dominates every
prescribed lower bound, nor that every admissible \(q\) has such a
representation.  Instead they give an unconditional symbolic solution on the
explicit infinite family \(q=(14r+9)^2\), and that one solution simultaneously
works for all full lifts of the common normalizing translate.

This closes only the stated integer relaxation.  A weighted vector satisfying
all quotient shifts does not choose points inside its fibers and does not force
the individual nonzero differences in \(C_v\) to occur once each.  Hence the
result neither constructs a cyclic PDS nor embeds \(A_*\), and it proves no
new nonexistence order.  It does not resolve the actual arbitrary-cyclic-PDS
embedding question for \(A_*\), the four-point statement \((F_4)\), or the
project's `publishable` target.  Hall's quotient equations and multiplier input
are known; the exact collision-sensitive all-translate packaging and this
method-boundary theorem remain novelty-unverified pending a dedicated source
audit.
