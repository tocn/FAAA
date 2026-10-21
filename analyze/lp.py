"""LP 求解器封装。

优先用 Google OR-Tools（与 YAFC 同一个求解器），环境里没有时退回内置两阶段单纯形。

接口保持简单：
    lp = LP(n).le({i: c}, rhs).eq(...).ge(...).obj({i: c})
    status, x, obj = lp.solve()

    status: "optimal" / "infeasible" / "unbounded" / "error"
    obj   : minimize c^T x 的值（要 maximize 就传负系数）
    integer=True 时用整数变量（机器台数这类场景）
"""
from __future__ import annotations

EPS = 1e-9
MAX_ITER = 5000

try:
    from ortools.linear_solver import pywraplp

    HAS_ORTOOLS = True
except ImportError:  # pragma: no cover
    HAS_ORTOOLS = False


class LP:
    def __init__(self, num_vars: int, name: str = "lp", integer: bool = False):
        self.n = num_vars
        self.name = name
        self.integer = integer
        self.rows: list[tuple[list[float], str, float]] = []
        self.c: list[float] = [0.0] * num_vars

    def _coef(self, d) -> list[float]:
        v = [0.0] * self.n
        if isinstance(d, dict):
            for k, val in d.items():
                v[k] += val
        else:
            for i, val in enumerate(d):
                if val:
                    v[i] += val
        return v

    def le(self, d, rhs: float):
        self.rows.append((self._coef(d), "<=", rhs))
        return self

    def eq(self, d, rhs: float):
        self.rows.append((self._coef(d), "=", rhs))
        return self

    def ge(self, d, rhs: float):
        self.rows.append((self._coef(d), ">=", rhs))
        return self

    def obj(self, d):
        """给 minimize 的系数；要 maximize 就传负值。"""
        self.c = self._coef(d)
        return self

    def solve(self):
        if HAS_ORTOOLS:
            return self._solve_ortools()
        return self._solve_simplex()

    # ---------- OR-Tools ----------
    def _solve_ortools(self):
        solver = pywraplp.Solver.CreateSolver("SCIP" if self.integer else "GLOP")
        if solver is None:
            return self._solve_simplex()
        inf = solver.infinity()
        x = []
        for i in range(self.n):
            if self.integer:
                x.append(solver.IntVar(0, inf, f"x{i}"))
            else:
                x.append(solver.NumVar(0, inf, f"x{i}"))

        for coefs, op, rhs in self.rows:
            expr = solver.Sum(coefs[i] * x[i] for i in range(self.n) if coefs[i])
            if op == "<=":
                solver.Add(expr <= rhs)
            elif op == ">=":
                solver.Add(expr >= rhs)
            else:
                solver.Add(expr == rhs)

        solver.Minimize(solver.Sum(self.c[i] * x[i] for i in range(self.n) if self.c[i]))
        st = solver.Solve()
        if st == pywraplp.Solver.OPTIMAL:
            vals = [v.solution_value() for v in x]
            return "optimal", vals, sum(c * v for c, v in zip(self.c, vals))
        if st == pywraplp.Solver.INFEASIBLE:
            return "infeasible", None, None
        if st == pywraplp.Solver.UNBOUNDED:
            return "unbounded", None, None
        return "error", None, None

    # ---------- 内置单纯形（无 ortools 时） ----------
    def _solve_simplex(self):
        n = self.n
        rows = []
        for coefs, op, rhs in self.rows:
            c = list(coefs)
            if rhs < 0:
                c = [-v for v in c]
                rhs = -rhs
                op = {"<=": ">=", ">=": "<=", "=": "="}[op]
            rows.append((c, op, rhs))

        n_slack = sum(1 for _, op, _ in rows if op in ("<=", ">="))
        n_art = sum(1 for _, op, _ in rows if op in (">=", "="))
        total = n + n_slack + n_art
        T, basis = [], []
        s_idx, a_idx = n, n + n_slack
        for coefs, op, rhs in rows:
            row = coefs + [0.0] * (total - n)
            if op == "<=":
                row[s_idx] = 1.0
                basis.append(s_idx)
                s_idx += 1
            elif op == ">=":
                row[s_idx] = -1.0
                s_idx += 1
                row[a_idx] = 1.0
                basis.append(a_idx)
                a_idx += 1
            else:
                row[a_idx] = 1.0
                basis.append(a_idx)
                a_idx += 1
            T.append(row + [rhs])

        def pivot(tr, tc):
            p = T[tr][tc]
            T[tr] = [v / p for v in T[tr]]
            for i in range(len(T)):
                if i != tr and abs(T[i][tc]) > EPS:
                    f = T[i][tc]
                    T[i] = [a - f * b for a, b in zip(T[i], T[tr])]

        def simplex(allowed, phase1=False):
            for _ in range(MAX_ITER):
                if phase1:
                    cost = [0.0] * total
                    for j in range(n + n_slack, total):
                        cost[j] = 1.0
                else:
                    cost = [0.0] * total
                    for j in range(n):
                        cost[j] = self.c[j]
                cz = list(cost)
                for i, bj in enumerate(basis):
                    if abs(cz[bj]) > EPS:
                        f = cz[bj]
                        cz = [a - f * b for a, b in zip(cz, T[i][:-1])]
                enter = next((j for j in allowed if cz[j] < -EPS), -1)
                if enter < 0:
                    return True
                best, ratio = -1, float("inf")
                for i in range(len(T)):
                    aij = T[i][enter]
                    if aij > EPS and T[i][-1] / aij < ratio - EPS:
                        ratio, best = T[i][-1] / aij, i
                if best < 0:
                    return False
                pivot(best, enter)
                basis[best] = enter
            return False

        art = list(range(n + n_slack, total))
        if art:
            if not simplex(range(total), phase1=True):
                return "infeasible", None, None
            if sum(T[i][-1] for i in range(len(T)) if basis[i] in art) > 1e-6:
                return "infeasible", None, None
            for i in range(len(T)):
                if basis[i] in art:
                    for j in range(total):
                        if j not in art and abs(T[i][j]) > EPS:
                            pivot(i, j)
                            basis[i] = j
                            break
        if not simplex([j for j in range(total) if j not in art]):
            return "unbounded", None, None
        x = [0.0] * n
        for i, bj in enumerate(basis):
            if bj < n:
                x[bj] = T[i][-1]
        return "optimal", x, sum(c * v for c, v in zip(self.c, x))


def _selftest():
    lp = LP(2).le({0: 1, 1: 2}, 4).le({0: 1}, 3).obj({0: -1, 1: -1})
    st, x, o = lp.solve()
    assert st == "optimal" and abs(-o - 3.5) < 1e-6, (st, x, o)

    lp = LP(2).eq({0: 1, 1: 1}, 5).ge({0: 1, 1: -1}, 1).obj({0: 1})
    st, x, o = lp.solve()
    assert st == "optimal" and abs(o - 3.0) < 1e-6, (st, x, o)

    lp = LP(1).ge({0: 1}, 10).le({0: 1}, 1).obj({0: 1})
    assert lp.solve()[0] == "infeasible"

    print("lp selftest ok | backend:", "ortools" if HAS_ORTOOLS else "simplex")


if __name__ == "__main__":
    _selftest()
