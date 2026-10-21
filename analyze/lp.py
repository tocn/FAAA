"""纯标准库的两阶段单纯形求解器。

支持:
    min c^T x
    s.t.  A_ub x <= b_ub,  A_eq x = b_eq,  A_ge x >= b_ge
          x >= 0

规模按几十个变量设计（稠密表），不做稀疏优化。
数值容差 EPS 取 1e-9；退化时用 Bland 规则防循环。
"""
from __future__ import annotations

EPS = 1e-9
MAX_ITER = 5000


class LP:
    def __init__(self, num_vars: int, name: str = ""):
        self.n = num_vars
        self.name = name
        self.rows: list[tuple[list[float], str, float]] = []
        self.c: list[float] = [0.0] * num_vars

    def _coef(self, d: dict[int, float] | list[float]) -> list[float]:
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
        """直接给 minimize 的系数；要 maximize 就传负值。"""
        self.c = self._coef(d)
        return self

    # ---------- 求解 ----------
    def solve(self):
        n = self.n
        rows = []
        for coefs, op, rhs in self.rows:
            c = list(coefs)
            if rhs < 0:
                c = [-x for x in c]
                rhs = -rhs
                op = {"<=": ">=", ">=": "<=", "=": "="}[op]
            rows.append((c, op, rhs))

        slack, art = [], []
        n_slack = sum(1 for _, op, _ in rows if op in ("<=", ">="))
        n_art = sum(1 for _, op, _ in rows if op in (">=", "="))
        total = n + n_slack + n_art
        T = []
        basis = []
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
            piv = T[tr][tc]
            T[tr] = [v / piv for v in T[tr]]
            for i in range(len(T)):
                if i != tr and abs(T[i][tc]) > EPS:
                    f = T[i][tc]
                    T[i] = [a - f * b for a, b in zip(T[i], T[tr])]

        def simplex(allowed, phase1=False):
            for _ in range(MAX_ITER):
                # 目标行：cz - c
                if phase1:
                    # 最小化人工变量之和
                    cost = [0.0] * total
                    for j in range(n + n_slack, total):
                        cost[j] = 1.0
                else:
                    cost = [0.0] * total
                    for j in range(n):
                        cost[j] = self.c[j]
                # 计算 reduced cost（对基变量消元）
                cz = list(cost)
                for i, bj in enumerate(basis):
                    if abs(cz[bj]) > EPS:
                        f = cz[bj]
                        cz = [a - f * b for a, b in zip(cz, T[i][:-1])]
                enter = -1
                for j in allowed:
                    if cz[j] < -EPS:
                        enter = j
                        break
                if enter < 0:
                    return True
                best, ratio = -1, float("inf")
                for i in range(len(T)):
                    aij = T[i][enter]
                    if aij > EPS:
                        r = T[i][-1] / aij
                        if r < ratio - EPS:
                            ratio, best = r, i
                if best < 0:
                    return False
                pivot(best, enter)
                basis[best] = enter
            return False

        art_cols = list(range(n + n_slack, total))
        if art_cols:
            if not simplex(range(total), phase1=True):
                return "infeasible", None, None
            val = sum(T[i][-1] for i in range(len(T)) if basis[i] in art_cols)
            if val > 1e-6:
                return "infeasible", None, None
            # 驱除人工变量
            for i in range(len(T)):
                if basis[i] in art_cols:
                    for j in range(total):
                        if j not in art_cols and abs(T[i][j]) > EPS:
                            pivot(i, j)
                            basis[i] = j
                            break

        allowed = [j for j in range(total) if j not in art_cols]
        if not simplex(allowed):
            return "unbounded", None, None

        x = [0.0] * n
        for i, bj in enumerate(basis):
            if bj < n:
                x[bj] = T[i][-1]
        obj = sum(ci * xi for ci, xi in zip(self.c, x))
        return "optimal", x, obj


def _selftest():
    # max x1 + x2 ; x1 + 2x2 <= 4 ; x1 <= 3
    lp = LP(2).le({0: 1, 1: 2}, 4).le({0: 1}, 3)
    lp.obj({0: -1, 1: -1})
    st, x, o = lp.solve()
    assert st == "optimal", st
    assert abs(-o - 3.5) < 1e-6, (x, o)

    # 等式 + 非零下界：x1 + x2 = 5, x1 - x2 >= 1, min x1
    lp = LP(2).eq({0: 1, 1: 1}, 5).ge({0: 1, 1: -1}, 1)
    lp.obj({0: 1})
    st, x, o = lp.solve()
    assert st == "optimal", st
    assert abs(o - 3.0) < 1e-6, (x, o)
    print("lp selftest ok")


if __name__ == "__main__":
    _selftest()
