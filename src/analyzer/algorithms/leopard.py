"""
LEOPARD-style function risk analysis algorithm.
==============================================

This plugin ports the existing LEOPARD AST metric extractor to the LAFVT
analyzer framework and exposes it as ``--algorithm leopard``.

Scoring model (binned)
----------------------
1. Compute raw LEOPARD metrics per function:
   * Complexity family: C1..C4
   * Vulnerability family: V1..V11
2. Compute:
   * ``complexity_score = C1 + C2 + C3 + C4``
   * ``vulnerability_score = V1 + ... + V11``
3. Place functions into bins by exact ``complexity_score`` value
   (same score => same bin).
4. Normalise ``vulnerability_score`` *within each complexity bin*.
5. Build final ``score`` so higher-complexity bins are always prioritised,
   while preserving vulnerability ordering inside each bin.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

import clang.cindex as cindex
from clang.cindex import CursorKind, TypeKind
import pandas as pd

from analyzer.base import AnalysisAlgorithm, register_algorithm

logger = logging.getLogger(__name__)

_C_EXTENSIONS: Set[str] = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx"}
@dataclass
class FunctionMetrics:
    tu_path: str
    file_path: str
    func_name: str
    start_line: int
    end_line: int

    # Complexity metrics
    C1: int = 1
    C2: int = 0
    C3: int = 0
    C4: int = 0

    # Vulnerability metrics
    V1: int = 0
    V2: int = 0
    V3: int = 0
    V4: int = 0
    V5: int = 0
    V6: int = 0
    V7: int = 0
    V8: int = 0
    V9: int = 0
    V10: int = 0
    V11: int = 0

    def complexity_score(self) -> int:
        return self.C1 + self.C2 + self.C3 + self.C4

    def vulnerability_score(self) -> int:
        return (
            self.V1
            + self.V2
            + self.V3
            + self.V4
            + self.V5
            + self.V6
            + self.V7
            + self.V8
            + self.V9
            + self.V10
            + self.V11
        )


class _ControlNode:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor
        self.children: List[_ControlNode] = []
        self.vars_in_cond: Set[str] = set()


class _AnalysisState:
    def __init__(self, fm: FunctionMetrics) -> None:
        self.fm = fm
        self.loop_depth = 0
        self.control_stack: List[_ControlNode] = []
        self.all_control_nodes: List[_ControlNode] = []
        self.vars_to_controls: DefaultDict[str, Set[_ControlNode]] = defaultdict(set)
        self.vars_in_any_predicate: Set[str] = set()
        self.ptr_op_counts: DefaultDict[str, int] = defaultdict(int)
        self.call_arg_vars: Set[str] = set()


def _configure_libclang() -> None:
    """
    Configure libclang if possible.

    Behaviour:
    * If ``LIBCLANG_FILE`` is set, prefer it.
    * Else try the bundled wheel location ``clang/native``.
    * Else rely on system discovery by clang.cindex.
    """
    if getattr(cindex.Config, "loaded", False):
        return

    env_path = os.environ.get("LIBCLANG_FILE")
    if env_path:
        try:
            cindex.Config.set_library_file(env_path)
            logger.debug("[leopard] Using LIBCLANG_FILE=%s", env_path)
            return
        except Exception:
            logger.exception("[leopard] Failed to set LIBCLANG_FILE=%s", env_path)

    clang_pkg_dir = Path(cindex.__file__).resolve().parent
    native_dir = clang_pkg_dir / "native"
    if not native_dir.is_dir():
        logger.debug("[leopard] clang/native not found; falling back to system libclang")
        return

    if sys.platform.startswith("linux"):
        candidates = [
            "libclang.so",
            "libclang.so.1",
            *sorted(p.name for p in native_dir.glob("libclang.so*")),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "libclang.dylib",
            *sorted(p.name for p in native_dir.glob("libclang*.dylib")),
        ]
    else:
        candidates = [
            "libclang.dll",
            *sorted(p.name for p in native_dir.glob("libclang*.dll")),
        ]

    for name in candidates:
        lib_path = native_dir / name
        if not lib_path.exists():
            continue
        try:
            cindex.Config.set_library_file(str(lib_path))
            logger.debug("[leopard] Using bundled libclang: %s", lib_path)
            return
        except Exception:
            logger.debug("[leopard] Failed candidate libclang: %s", lib_path, exc_info=True)

    logger.debug("[leopard] No explicit libclang configured; relying on system loader")


def _load_compile_commands(ccdb_path: Path) -> List[Dict[str, Any]]:
    with ccdb_path.open(encoding="utf-8") as f:
        return json.load(f)


def _clean_args(raw_args: List[str]) -> List[str]:
    args = list(raw_args)
    if args and any(args[0].endswith(x) for x in ("clang", "clang++", "gcc", "g++", "cc", "c++")):
        args = args[1:]

    cleaned: List[str] = []
    it = iter(args)

    for a in it:
        if a == "-include":
            cleaned.append(a)
            try:
                cleaned.append(next(it))
            except StopIteration:
                break
            continue
        if a.startswith("-I") or a.startswith("-D") or a.startswith("-U"):
            cleaned.append(a)
            continue
        if a.startswith("-std=") or a.startswith("-m"):
            cleaned.append(a)
            continue
        if a in ("-MD", "-MMD", "-MP", "-MF", "-MT", "-MQ"):
            try:
                _ = next(it)
            except StopIteration:
                pass
            continue

    return cleaned


def _infer_target_from_args(raw_args: List[str]) -> Optional[str]:
    if not raw_args:
        return None
    compiler = Path(raw_args[0]).name
    for suffix in ("-gcc", "-g++", "-clang", "-clang++"):
        if compiler.endswith(suffix):
            return compiler[: -len(suffix)]
    return None


def _build_args_for_tu(
    src: Path,
    project_root: Path,
    *,
    target: Optional[str] = None,
    sysroot: Optional[str] = None,
    std: str = "c11",
) -> List[str]:
    src = src.resolve()
    project_root = project_root.resolve()

    args = ["-x", "c", f"-std={std}", "-ferror-limit=0"]
    args += [f"-I{src.parent}", f"-I{project_root}", f"-I{project_root / 'include'}"]

    if target:
        args += ["-target", target]
    if sysroot:
        args += ["--sysroot", sysroot]
    return args


def _is_pointer_cursor(cur: Any) -> bool:
    try:
        return cur.type.kind == TypeKind.POINTER
    except Exception:
        return False


def _record_pointer_op(state: _AnalysisState, var_cursor: Any) -> None:
    name = var_cursor.spelling
    if not name:
        return
    state.fm.V3 += 1
    state.ptr_op_counts[name] += 1


def _count_descendants(node: _ControlNode) -> int:
    total = 0
    for child in node.children:
        total += 1 + _count_descendants(child)
    return total


def _handle_decl_ref_in_condition(state: _AnalysisState, var_cursor: Any) -> None:
    if not state.control_stack:
        return
    name = var_cursor.spelling
    if not name:
        return
    node = state.control_stack[-1]
    node.vars_in_cond.add(name)
    state.vars_in_any_predicate.add(name)
    state.vars_to_controls[name].add(node)


def _visit_expr_for_logical_ops(cur: Any, state: _AnalysisState) -> None:
    for tok in (t.spelling for t in cur.get_tokens()):
        if tok in ("&&", "||"):
            state.fm.C1 += 1


def _visit(cursor: Any, state: _AnalysisState, in_condition: bool = False) -> None:
    kind = cursor.kind

    if kind in (
        CursorKind.IF_STMT,
        CursorKind.FOR_STMT,
        CursorKind.WHILE_STMT,
        CursorKind.DO_STMT,
        CursorKind.SWITCH_STMT,
    ):
        _visit_control_structure(
            cursor,
            state,
            loop_like=kind in (CursorKind.FOR_STMT, CursorKind.WHILE_STMT, CursorKind.DO_STMT),
        )
        return

    if kind in (CursorKind.CASE_STMT, CursorKind.DEFAULT_STMT, CursorKind.CONDITIONAL_OPERATOR):
        state.fm.C1 += 1

    if kind == CursorKind.BINARY_OPERATOR:
        _visit_expr_for_logical_ops(cursor, state)

    if kind == CursorKind.UNARY_OPERATOR:
        tokens = [t.spelling for t in cursor.get_tokens()]

        if any(tok in ("*", "++", "--") for tok in tokens):
            for sub in cursor.walk_preorder():
                if sub.kind == CursorKind.DECL_REF_EXPR and _is_pointer_cursor(sub) and sub.spelling:
                    _record_pointer_op(state, sub)
                    break

        if "&" in tokens:
            for sub in cursor.walk_preorder():
                if sub.kind == CursorKind.DECL_REF_EXPR and sub.spelling:
                    _record_pointer_op(state, sub)
                    break

    if kind == CursorKind.MEMBER_REF_EXPR:
        tokens = [t.spelling for t in cursor.get_tokens()]
        if "->" in tokens:
            for sub in cursor.walk_preorder():
                if sub.kind == CursorKind.DECL_REF_EXPR and _is_pointer_cursor(sub) and sub.spelling:
                    _record_pointer_op(state, sub)
                    break

    if kind == CursorKind.ARRAY_SUBSCRIPT_EXPR:
        for sub in cursor.walk_preorder():
            if sub.kind == CursorKind.DECL_REF_EXPR and _is_pointer_cursor(sub) and sub.spelling:
                _record_pointer_op(state, sub)
                break

    if kind == CursorKind.BINARY_OPERATOR:
        children = list(cursor.get_children())
        tokens = [t.spelling for t in cursor.get_tokens()]
        if len(children) == 2:
            lhs, rhs = children

            if any(tok in ("+", "-") for tok in tokens):
                if _is_pointer_cursor(lhs) or _is_pointer_cursor(rhs):
                    for side in (lhs, rhs):
                        for sub in side.walk_preorder():
                            if sub.kind == CursorKind.DECL_REF_EXPR and _is_pointer_cursor(sub) and sub.spelling:
                                _record_pointer_op(state, sub)
                                break
                        else:
                            continue
                        break

            if any(tok in ("==", "!=", "<", "<=", ">", ">=") for tok in tokens):
                if _is_pointer_cursor(lhs) or _is_pointer_cursor(rhs):
                    for side in (lhs, rhs):
                        for sub in side.walk_preorder():
                            if sub.kind == CursorKind.DECL_REF_EXPR and _is_pointer_cursor(sub) and sub.spelling:
                                _record_pointer_op(state, sub)
                                break

    if kind == CursorKind.CALL_EXPR:
        call_children = list(cursor.get_children())
        arg_nodes = call_children[1:] if len(call_children) >= 2 else []
        for arg in arg_nodes:
            if arg.kind == CursorKind.DECL_REF_EXPR and arg.spelling:
                state.call_arg_vars.add(arg.spelling)
            else:
                for sub in arg.walk_preorder():
                    if sub.kind == CursorKind.DECL_REF_EXPR and sub.spelling:
                        state.call_arg_vars.add(sub.spelling)

    if in_condition and kind == CursorKind.DECL_REF_EXPR:
        _handle_decl_ref_in_condition(state, cursor)

    for child in cursor.get_children():
        _visit(child, state, in_condition=in_condition)


def _visit_control_structure(cur: Any, state: _AnalysisState, loop_like: bool = False) -> None:
    state.fm.C1 += 1

    depth = len(state.control_stack) + 1
    state.fm.V7 = max(state.fm.V7, depth)

    node = _ControlNode(cur)
    if state.control_stack:
        state.control_stack[-1].children.append(node)
    state.control_stack.append(node)
    state.all_control_nodes.append(node)

    if loop_like:
        state.loop_depth += 1
        state.fm.C2 += 1
        if state.loop_depth > 1:
            state.fm.C3 += 1
        state.fm.C4 = max(state.fm.C4, state.loop_depth)

    children = list(cur.get_children())
    if cur.kind in (CursorKind.IF_STMT, CursorKind.WHILE_STMT):
        if children:
            _visit(children[0], state, in_condition=True)
            for body_child in children[1:]:
                _visit(body_child, state, in_condition=False)
    elif cur.kind == CursorKind.FOR_STMT:
        if len(children) >= 2:
            _visit(children[0], state, in_condition=False)
            _visit(children[1], state, in_condition=True)
            for other in children[2:]:
                _visit(other, state, in_condition=False)
        else:
            for ch in children:
                _visit(ch, state, in_condition=False)
    elif cur.kind == CursorKind.SWITCH_STMT:
        if children:
            _visit(children[0], state, in_condition=True)
            for body_child in children[1:]:
                _visit(body_child, state, in_condition=False)
    else:
        for ch in children:
            _visit(ch, state, in_condition=False)

    if cur.kind == CursorKind.IF_STMT:
        has_else = len(children) >= 3
        if not has_else:
            state.fm.V10 += 1

    if loop_like:
        state.loop_depth -= 1
    state.control_stack.pop()

# poopy
def _analyze_function(func_cursor: Any, tu_path: str) -> FunctionMetrics:
    loc = func_cursor.location
    file_path = Path(loc.file.name).resolve() if loc.file else Path(tu_path).resolve()

    start_line = int(loc.line or 0)
    end_line = int(getattr(func_cursor.extent.end, "line", start_line) or start_line)
    end_line = max(start_line, end_line)

    fm = FunctionMetrics(
        tu_path=tu_path,
        file_path=file_path.as_posix(),
        func_name=func_cursor.spelling,
        start_line=start_line,
        end_line=end_line,
    )

    fm.V1 = len(list(func_cursor.get_arguments()))
    state = _AnalysisState(fm)

    body = None
    for ch in func_cursor.get_children():
        if ch.kind == CursorKind.COMPOUND_STMT:
            body = ch
            break
    if body is None:
        return fm

    def _walk(cur: Any) -> None:
        k = cur.kind
        if k in (CursorKind.IF_STMT, CursorKind.SWITCH_STMT):
            _visit_control_structure(cur, state, loop_like=False)
        elif k in (CursorKind.FOR_STMT, CursorKind.WHILE_STMT, CursorKind.DO_STMT):
            _visit_control_structure(cur, state, loop_like=True)
        else:
            _visit(cur, state, in_condition=False)

    for child in body.get_children():
        _walk(child)

    fm.V6 = sum(_count_descendants(node) for node in state.all_control_nodes)
    fm.V4 = len([v for v, c in state.ptr_op_counts.items() if c > 0])
    fm.V5 = max(state.ptr_op_counts.values()) if state.ptr_op_counts else 0

    for node in state.all_control_nodes:
        fm.V8 = max(fm.V8, _count_descendants(node))
    if state.vars_to_controls:
        fm.V9 = max(len(s) for s in state.vars_to_controls.values())

    fm.V2 = len(state.call_arg_vars)
    fm.V11 = len(state.vars_in_any_predicate)

    return fm


@register_algorithm
class LeopardAlgorithm(AnalysisAlgorithm):
    """
    LEOPARD AST-based vulnerability prioritisation with complexity binning.

    Register name: ``"leopard"``.
    """

    name = "leopard"

    def __init__(self) -> None:
        _configure_libclang()
        self._index = cindex.Index.create()

    def analyze(self, root_directory: Path) -> pd.DataFrame:
        root_directory = Path(root_directory).resolve()
        if not root_directory.is_dir():
            raise ValueError(f"[leopard] Not a directory: {root_directory}")

        logger.info("[leopard] Starting analysis of: %s", root_directory)

        ccdb_path = root_directory / "compile_commands.json"
        if ccdb_path.is_file():
            logger.info("[leopard] Using compile_commands.json at %s", ccdb_path)
            metrics = self._analyze_with_ccdb(root_directory, ccdb_path)
        else:
            logger.info("[leopard] No compile_commands.json found; using best-effort parsing")
            metrics = self._analyze_without_ccdb(root_directory)

        if not metrics:
            logger.warning("[leopard] No functions found in %s", root_directory)
            return pd.DataFrame(columns=list(self.REQUIRED_COLUMNS))

        rows = [self._to_row(m) for m in metrics]
        df = pd.DataFrame(rows)
        df.drop_duplicates(
            subset=["filepath", "function_name", "start_line", "end_line"],
            inplace=True,
        )

        # Complexity bins, then vulnerability ranking within each bin.
        df = self._apply_binned_scoring(df)
        df.sort_values(["score", "vulnerability_score"], ascending=[False, False], inplace=True)

        logger.info("[leopard] Analysis complete: %d functions", len(df))
        return self._validate_output(df)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _analyze_with_ccdb(
        self,
        root_directory: Path,
        ccdb_path: Path,
    ) -> List[FunctionMetrics]:
        ccdb = _load_compile_commands(ccdb_path)

        target_override = os.environ.get("LAFVT_LEOPARD_TARGET")
        sysroot_override = os.environ.get("LAFVT_LEOPARD_SYSROOT")

        seen_funcs: Set[Tuple[str, int, str]] = set()
        all_metrics: List[FunctionMetrics] = []

        for entry in ccdb:
            src = self._resolve_ccdb_source(entry, root_directory)
            if src is None or not src.is_file():
                continue
            if src.suffix.lower() not in _C_EXTENSIONS:
                continue

            raw_args = self._extract_raw_args(entry)
            args_for_tu = _clean_args(raw_args)

            inferred_target = _infer_target_from_args(raw_args)
            target = target_override or inferred_target
            if target:
                args_for_tu += ["-target", target]
            if sysroot_override:
                args_for_tu += ["--sysroot", sysroot_override]

            tu = self._safe_parse(src, args_for_tu)
            if tu is None:
                continue

            self._collect_metrics_from_tu(
                tu=tu,
                tu_path=src.as_posix(),
                seen_funcs=seen_funcs,
                all_metrics=all_metrics,
            )

        return all_metrics

    def _analyze_without_ccdb(self, root_directory: Path) -> List[FunctionMetrics]:
        target_override = os.environ.get("LAFVT_LEOPARD_TARGET")
        sysroot_override = os.environ.get("LAFVT_LEOPARD_SYSROOT")
        std_override = os.environ.get("LAFVT_LEOPARD_STD", "c11")

        source_files = sorted(
            p for p in root_directory.rglob("*") if p.is_file() and p.suffix.lower() in _C_EXTENSIONS
        )
        if not source_files:
            return []

        seen_funcs: Set[Tuple[str, int, str]] = set()
        all_metrics: List[FunctionMetrics] = []

        for src in source_files:
            args_for_tu = _build_args_for_tu(
                src=src,
                project_root=root_directory,
                target=target_override,
                sysroot=sysroot_override,
                std=std_override,
            )
            tu = self._safe_parse(src, args_for_tu)
            if tu is None:
                continue

            self._collect_metrics_from_tu(
                tu=tu,
                tu_path=src.as_posix(),
                seen_funcs=seen_funcs,
                all_metrics=all_metrics,
            )

        return all_metrics

    def _safe_parse(self, src: Path, args_for_tu: List[str]) -> Optional[Any]:
        try:
            return self._index.parse(str(src), args=args_for_tu)
        except cindex.TranslationUnitLoadError:
            logger.warning("[leopard] Failed to parse TU: %s", src)
            return None
        except Exception:
            logger.exception("[leopard] Unexpected parse error for: %s", src)
            return None

    @staticmethod
    def _resolve_ccdb_source(entry: Dict[str, Any], root_directory: Path) -> Optional[Path]:
        file_value = entry.get("file")
        if not file_value:
            return None

        src = Path(str(file_value))
        base_dir = Path(str(entry.get("directory", root_directory))).resolve()
        if not src.is_absolute():
            src = (base_dir / src).resolve()
        else:
            src = src.resolve()
        return src

    @staticmethod
    def _extract_raw_args(entry: Dict[str, Any]) -> List[str]:
        if "arguments" in entry and isinstance(entry["arguments"], list):
            return [str(x) for x in entry["arguments"]]
        if "command" in entry and isinstance(entry["command"], str):
            return shlex.split(entry["command"])
        return []

    @staticmethod
    def _collect_metrics_from_tu(
        *,
        tu: Any,
        tu_path: str,
        seen_funcs: Set[Tuple[str, int, str]],
        all_metrics: List[FunctionMetrics],
    ) -> None:
        for cur in tu.cursor.walk_preorder():
            if cur.kind != CursorKind.FUNCTION_DECL or not cur.is_definition():
                continue
            if not cur.spelling:
                continue

            loc = cur.location
            if not loc.file:
                continue

            fpath = Path(loc.file.name).resolve()
            if fpath.suffix.lower() not in _C_EXTENSIONS:
                continue

            key = (fpath.as_posix(), int(loc.line or 0), cur.spelling)
            if key in seen_funcs:
                continue
            seen_funcs.add(key)

            all_metrics.append(_analyze_function(cur, tu_path=tu_path))

    @staticmethod
    def _to_row(fm: FunctionMetrics) -> Dict[str, Any]:
        return {
            "filepath": fm.file_path,
            "function_name": fm.func_name,
            "start_line": fm.start_line,
            "end_line": fm.end_line,
            "C1": fm.C1,
            "C2": fm.C2,
            "C3": fm.C3,
            "C4": fm.C4,
            "V1": fm.V1,
            "V2": fm.V2,
            "V3": fm.V3,
            "V4": fm.V4,
            "V5": fm.V5,
            "V6": fm.V6,
            "V7": fm.V7,
            "V8": fm.V8,
            "V9": fm.V9,
            "V10": fm.V10,
            "V11": fm.V11,
            "complexity_score": float(fm.complexity_score()),
            "vulnerability_score": float(fm.vulnerability_score()),
        }
    # poopy
    @staticmethod
    def _apply_binned_scoring(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            df["score"] = []
            return df

        # LEOPARD-style binning: functions with the same complexity score
        # are grouped into the same bin (no range/quantile binning).
        try:
            complexity = df["complexity_score"].astype(float)
            unique_scores = sorted(complexity.dropna().unique().tolist())
            score_to_bin = {score: idx for idx, score in enumerate(unique_scores)}
            df["bin"] = complexity.map(score_to_bin).fillna(0).astype(int)
        except Exception:
            logger.exception("[leopard] Complexity binning failed; using a single bin")
            df["bin"] = 0

        vuln_min = df.groupby("bin")["vulnerability_score"].transform("min")
        vuln_max = df.groupby("bin")["vulnerability_score"].transform("max")
        vuln_denom = (vuln_max - vuln_min).replace(0, 1.0)
        df["norm_vulnerability"] = (df["vulnerability_score"] - vuln_min) / vuln_denom

        c_min = float(df["complexity_score"].min())
        c_max = float(df["complexity_score"].max())
        c_denom = (c_max - c_min) if c_max != c_min else 1.0
        df["norm_complexity"] = (df["complexity_score"] - c_min) / c_denom

        # Lexicographic-like score:
        # - Integer part: complexity bin (higher complexity priority).
        # - Fractional part: vulnerability rank inside the bin.
        # - Tiny tie-breaker: global complexity normalisation.
        df["score"] = (
            df["bin"].astype(float)
            + df["norm_vulnerability"].astype(float)
            + (0.001 * df["norm_complexity"].astype(float))
        )

        return df
