from dataclasses import dataclass
from collections import defaultdict
import argparse
import json
from pathlib import Path
import shlex

import clang.cindex as cindex
from clang.cindex import CursorKind, TypeKind

# adjust this if your libclang.so file is elsewhere
#cindex.Config.set_library_file("/usr/lib/llvm-20/lib/libclang.so")

import sys
import os

def configure_libclang():
    # Allow explicit override (nice for CI)
    if "LIBCLANG_FILE" in os.environ:
        cindex.Config.set_library_file(os.environ["LIBCLANG_FILE"])
        return

    # Look for the bundled library inside site-packages/clang/native/
    clang_pkg_dir = Path(cindex.__file__).resolve().parent  # .../site-packages/clang
    native_dir = clang_pkg_dir / "native"
    if native_dir.is_dir():
        if sys.platform.startswith("linux"):
            candidates = ["libclang.so", "libclang.so.1"]
            # also accept versioned .so files (common in wheels)
            candidates += sorted([p.name for p in native_dir.glob("libclang.so*")])
        elif sys.platform == "darwin":
            candidates = ["libclang.dylib"]
            candidates += sorted([p.name for p in native_dir.glob("libclang*.dylib")])
        else:  # windows
            candidates = ["libclang.dll"]
            candidates += sorted([p.name for p in native_dir.glob("libclang*.dll")])

        for name in candidates:
            p = native_dir / name
            if p.exists():
                cindex.Config.set_library_file(str(p))
                return

    # If we get here, we didn't find it
    raise RuntimeError(
        f"Could not find bundled libclang in {native_dir}. "
        "Install via `pip install libclang` (bundled) or set LIBCLANG_FILE."
    )


ROOT = Path(__file__).resolve().parent
# compile_commands.json contains all the build flags needed to compile each file
# (such as include paths for any given file)
CCDB_PATH = ROOT / "compile_commands.json"

@dataclass
class FunctionMetrics:
    tu_path: str
    file_path: str
    func_name: str
    line: int

    # Complexity
    C1: int = 1   # cyclomatic complexity (start at 1)
    C2: int = 0   # #loops
    C3: int = 0   # #nested loops
    C4: int = 0   # max loop nesting depth

    # Vulnerability metrics
    V1: int = 0   # #parameter vars
    V2: int = 0   # #vars used as parameters in calls
    V3: int = 0   # #pointer arithmetic ops
    V4: int = 0   # #vars involved in pointer arithmetic
    V5: int = 0   # max pointer arithmetic count per var
    V6: int = 0   # #nested control structures
    V7: int = 0   # max nesting level of control structures
    V8: int = 0   # max control-dependent control structures
    V9: int = 0   # max data-dependent control structures
    V10: int = 0  # #if without else
    V11: int = 0  # #vars involved in control predicates

    def complexity_score(self) -> int:
        return self.C1 + self.C2 + self.C3 + self.C4

    def vulnerability_score(self) -> int:
        return (self.V1 + self.V2 + self.V3 + self.V4 + self.V5 +
                self.V6 + self.V7 + self.V8 + self.V9 + self.V10 + self.V11)

# Control node is a single control structure (if/for/while/switch)
class ControlNode:
    def __init__(self, cursor):
        self.cursor = cursor
        self.children = [] # nested control structures
        self.vars_in_cond = set() # variable names present in control structure

class AnalysisState:
    def __init__(self, fm: FunctionMetrics):
        self.fm = fm

        self.loop_depth = 0
        
        self.control_stack: List[ControlNode] = []
        self.all_control_nodes: list[ControlNode] = []
        self.vars_to_controls: dict[str, set[ControlNode]] = defaultdict(set)
        self.vars_in_any_predicate: set[str] = set()
        self.ptr_op_counts: dict[str, int] = defaultdict(int)
        self.call_arg_vars: set[str] = set()

# ======= Helpers to load compile_commands.json =======

def load_compile_commands():
    with CCDB_PATH.open() as f:
        return json.load(f)

def clean_args(raw_args):
    args = list(raw_args)

    # Drop compiler binary name if present
    if args and any(args[0].endswith(x) for x in ("clang", "clang-20", "gcc", "cc")):
        args = args[1:]

    cleaned = []
    it = iter(args)

    for a in it:
        # Keep -include and its argument
        if a == "-include":
            cleaned.append(a)
            try:
                cleaned.append(next(it))
            except StopIteration:
                break
            continue

        # Keep include paths
        if a.startswith("-I"):
            cleaned.append(a)
            continue

        # Keep macro defines / undefines
        if a.startswith("-D") or a.startswith("-U"):
            cleaned.append(a)
            continue

        # Keep language / arch flags
        if a.startswith("-std=") or a.startswith("-m"):
            cleaned.append(a)
            continue

        # Drop dependency-generation flags and their parameter
        if a in ("-MD", "-MMD", "-MP", "-MF", "-MT", "-MQ"):
            try:
                _ = next(it)  # skip the next arg (output file)
            except StopIteration:
                pass
            continue

        # Everything else (warnings, optimizations, debug, etc.) is dropped

    return cleaned

def is_in_dir(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False

# ======= AST analysis helpers =======

def is_pointer_cursor(cur) -> bool:
    try: 
        t = cur.type
    except:
        return False
    return t.kind == TypeKind.POINTER

def record_pointer_op(state: AnalysisState, var_cursor):
    """
    Record a pointer arithmetic operation (V3, V4, V5).
    """
    name = var_cursor.spelling
    if not name:
        return
    state.fm.V3 += 1
    state.ptr_op_counts[name] += 1


def count_descendants(node: ControlNode) -> int:
    """
    Count all descendant control structures (for V8).
    """
    total = 0
    for child in node.children:
        total += 1 + count_descendants(child)
    return total


def handle_decl_ref_in_condition(state: AnalysisState, var_cursor):
    """
    Called when we encounter a DeclRefExpr inside a control predicate.
    Updates vars_in_cond, vars_to_controls, vars_in_any_predicate.
    """
    if not state.control_stack:
        return
    name = var_cursor.spelling
    if not name:
        return
    node = state.control_stack[-1]
    node.vars_in_cond.add(name)
    state.vars_in_any_predicate.add(name)
    state.vars_to_controls[name].add(node)


def visit_expr_for_logical_ops(cur, state: AnalysisState):
    """
    Increment cyclomatic complexity for logical && and || operators.
    Very approximate: we just look for '&&' / '||' in the tokens.
    """
    tokens = [t.spelling for t in cur.get_tokens()]
    for tok in tokens:
        if tok in ("&&", "||"):
            state.fm.C1 += 1


def visit(cursor, state: AnalysisState, in_condition: bool = False):
    """
    Generic recursive AST visitor for a function.
    Updates FunctionMetrics in state.fm.
    """
    kind = cursor.kind

    # Handle cyclomatic complexity decision points (C1)
    if kind in (
        CursorKind.IF_STMT,
        CursorKind.FOR_STMT,
        CursorKind.WHILE_STMT,
        CursorKind.DO_STMT,
        CursorKind.SWITCH_STMT,
        CursorKind.CASE_STMT,
        CursorKind.DEFAULT_STMT,
        CursorKind.CONDITIONAL_OPERATOR,
    ):
        state.fm.C1 += 1

    # Count logical && / || as extra decisions
    if kind == CursorKind.BINARY_OPERATOR:
        visit_expr_for_logical_ops(cursor, state)

    # Pointer arithmetic detection (V3–V5, approximate)
    if kind == CursorKind.UNARY_OPERATOR:
        # e.g., *ptr, ++ptr, ptr++
        tokens = [t.spelling for t in cursor.get_tokens()]
        if any(tok in ("*", "++", "--") for tok in tokens):
            # find a pointer variable referenced below
            for child in cursor.get_children():
                if child.kind == CursorKind.DECL_REF_EXPR and is_pointer_cursor(child):
                    record_pointer_op(state, child)
                    break

    if kind in (CursorKind.MEMBER_REF_EXPR, CursorKind.MEMBER_REF_EXPR):
        # e.g., ptr->field
        for child in cursor.get_children():
            if child.kind == CursorKind.DECL_REF_EXPR and is_pointer_cursor(child):
                record_pointer_op(state, child)
                break

    if kind == CursorKind.BINARY_OPERATOR:
        # e.g., ptr + 1, ptr - 1
        children = list(cursor.get_children())
        if len(children) == 2:
            lhs, rhs = children
            tokens = [t.spelling for t in cursor.get_tokens()]
            if any(tok in ("+", "-") for tok in tokens):
                if is_pointer_cursor(lhs) or is_pointer_cursor(rhs):
                    # record for whichever is pointer-typed and a DeclRefExpr
                    for child in (lhs, rhs):
                        if child.kind == CursorKind.DECL_REF_EXPR and is_pointer_cursor(child):
                            record_pointer_op(state, child)
                            break

    # CallExpr: record variables used as arguments (V2)
    if kind == CursorKind.CALL_EXPR:
        for arg in cursor.get_children():
            # libclang usually puts the callee as the first child; args after that
            if arg.kind == CursorKind.DECL_REF_EXPR:
                state.call_arg_vars.add(arg.spelling)
            else:
                # in case args are more complex expressions, descend
                for sub in arg.walk_preorder():
                    if sub.kind == CursorKind.DECL_REF_EXPR:
                        state.call_arg_vars.add(sub.spelling)

    # DeclRefExpr inside a control predicate (V9, V11)
    if in_condition and kind == CursorKind.DECL_REF_EXPR:
        handle_decl_ref_in_condition(state, cursor)

    # Recurse by default
    for child in cursor.get_children():
        visit(child, state, in_condition=in_condition)


def visit_control_structure(cur, state: AnalysisState, loop_like: bool = False):
    """
    Handle generic control structures (if/for/while/do/switch):
    - Build control tree (for V6, V7, V8)
    - Track nesting depth (V6, V7)
    - Optionally track loop depth (C2–C4)
    - Visit condition separately with in_condition=True to collect predicate vars
    """
    # Control structures for V6/V7
    depth = len(state.control_stack) + 1
    state.fm.V7 = max(state.fm.V7, depth)
    if depth > 1:
        # count nested control structures; each level above contributes a pair
        state.fm.V6 += depth - 1

    node = ControlNode(cur)
    if state.control_stack:
        state.control_stack[-1].children.append(node)
    state.control_stack.append(node)
    state.all_control_nodes.append(node)

    # Loop metrics
    if loop_like:
        state.loop_depth += 1
        state.fm.C2 += 1
        if state.loop_depth > 1:
            state.fm.C3 += 1
        state.fm.C4 = max(state.fm.C4, state.loop_depth)

    # Heuristic for condition vs body:
    children = list(cur.get_children())
    if cur.kind in (CursorKind.IF_STMT, CursorKind.WHILE_STMT):
        if children:
            # first child is usually the condition
            cond = children[0]
            visit(cond, state, in_condition=True)
            for body_child in children[1:]:
                visit(body_child, state, in_condition=False)

    elif cur.kind == CursorKind.FOR_STMT:
        # Children are typically: init, condition, increment, body
        # We approximate: visit all, but treat the second child as condition if present
        if len(children) >= 2:
            init = children[0]
            cond = children[1]
            visit(init, state, in_condition=False)
            visit(cond, state, in_condition=True)
            for other in children[2:]:
                visit(other, state, in_condition=False)
        else:
            for ch in children:
                visit(ch, state, in_condition=False)

    elif cur.kind == CursorKind.SWITCH_STMT:
        if children:
            cond = children[0]
            visit(cond, state, in_condition=True)
            for body_child in children[1:]:
                visit(body_child, state, in_condition=False)
    else:
        # fallback: just visit normally
        for ch in children:
            visit(ch, state, in_condition=False)

    # If without else? (V10)
    if cur.kind == CursorKind.IF_STMT:
        has_else = any(ch.kind == CursorKind.IF_STMT or ch.kind == CursorKind.COMPOUND_STMT
                       for ch in children[1:])
        # above is a crude heuristic; better would be to inspect specific child roles
        # but this still gives you "ifs without any explicit else-like block"
        if not has_else:
            state.fm.V10 += 1

    # Pop control / loop
    if loop_like:
        state.loop_depth -= 1
    state.control_stack.pop()


def analyze_function(func_cursor, tu_path: str) -> FunctionMetrics:
    """
    Analyze a single function definition cursor and compute LEOPARD-style metrics.
    """
    loc = func_cursor.location
    file_path = Path(loc.file.name).resolve() if loc.file else ROOT
    fm = FunctionMetrics(
        tu_path=tu_path,
        file_path=str(file_path),
        func_name=func_cursor.spelling,
        line=loc.line,
    )

    # V1: #parameter variables
    fm.V1 = len(list(func_cursor.get_arguments()))

    state = AnalysisState(fm)

    # Find the function body (CompoundStmt) and start traversal there
    body = None
    for ch in func_cursor.get_children():
        if ch.kind == CursorKind.COMPOUND_STMT:
            body = ch
            break

    if body is None:
        return fm  # e.g., prototype only or weird declaration

    # Walk the body; intercept control structures explicitly so that we can
    # maintain the control tree and loop depth.
    def walk(cur):
        k = cur.kind
        if k in (CursorKind.IF_STMT, CursorKind.SWITCH_STMT):
            visit_control_structure(cur, state, loop_like=False)
        elif k in (CursorKind.FOR_STMT, CursorKind.WHILE_STMT, CursorKind.DO_STMT):
            visit_control_structure(cur, state, loop_like=True)
        else:
            visit(cur, state, in_condition=False)

    for child in body.get_children():
        walk(child)

    # Finalize pointer metrics (V4, V5)
    fm.V4 = len([v for v, c in state.ptr_op_counts.items() if c > 0])
    fm.V5 = max(state.ptr_op_counts.values()) if state.ptr_op_counts else 0

    # Finalize control-dependent metric (V8)
    for node in state.all_control_nodes:
        fm.V8 = max(fm.V8, count_descendants(node))

    # Finalize data-dependent metric (V9)
    if state.vars_to_controls:
        fm.V9 = max(len(s) for s in state.vars_to_controls.values())

    # Finalize V2 (vars used as parameters in calls)
    fm.V2 = len(state.call_arg_vars)

    # Finalize V11 (#vars involved in control predicates)
    fm.V11 = len(state.vars_in_any_predicate)

    return fm


# ---------------------------------------------------------------------------
# Main: parse TUs, analyze functions, print CSV
# ---------------------------------------------------------------------------

def main():

    configure_libclang()
    parser = argparse.ArgumentParser(
        description="Analyze C/C++ functions in a directory using LEOPARD-style metrics."
    )
    parser.add_argument(
        "target_dir",
        help="Directory containing C/C++ files to analyze "
             "(relative to the repo root or an absolute path).",
    )
    args = parser.parse_args()

    # Resolve target directory relative to ROOT if not absolute
    target_dir = Path(args.target_dir)
    if not target_dir.is_absolute():
        target_dir = (ROOT / target_dir).resolve()
    else:
        target_dir = target_dir.resolve()

    if not target_dir.exists():
        print(f"ERROR: target directory does not exist: {target_dir}")
        return

    print("ROOT:", ROOT)
    print("TARGET_DIR:", target_dir)

    ccdb = load_compile_commands()
    index = cindex.Index.create()

    seen_funcs = set()  # to avoid duplicates: (file, line, name)
    all_metrics: List[FunctionMetrics] = []
    num_functions = 0

    for entry in ccdb:
        # figure out source + args
        src = Path(entry["file"]).resolve()

        # Only parse TUs whose source files are under the target directory
        if not is_in_dir(src, target_dir):
            continue

        if "arguments" in entry:
            raw_args = entry["arguments"]
        else:
            raw_args = shlex.split(entry["command"])

        args_for_tu = clean_args(raw_args)

        # Parse TU
        try:
            tu = index.parse(str(src), args=args_for_tu)
        except cindex.TranslationUnitLoadError:
            print(f"WARNING: failed to parse TU for {src}")
            continue

        # Walk all function definitions in this TU
        for cur in tu.cursor.walk_preorder():
            if cur.kind == CursorKind.FUNCTION_DECL and cur.is_definition():
                loc = cur.location
                if not loc.file:
                    continue
                fpath = Path(loc.file.name).resolve()

                # Only consider functions whose file is under target_dir
                if not is_in_dir(fpath, target_dir):
                    continue

                key = (str(fpath), loc.line, cur.spelling)
                if key in seen_funcs:
                    continue
                seen_funcs.add(key)

                fm = analyze_function(cur, tu_path=str(src))
                all_metrics.append(fm)

    # Print CSV header
    print("file,func,line,C1,C2,C3,C4,V1,V2,V3,V4,V5,V6,V7,V8,V9,V10,V11,complexity_score,vulnerability_score")
    for fm in all_metrics:
        print(
            f"{fm.file_path},{fm.func_name},{fm.line},"
            f"{fm.C1},{fm.C2},{fm.C3},{fm.C4},"
            f"{fm.V1},{fm.V2},{fm.V3},{fm.V4},{fm.V5},"
            f"{fm.V6},{fm.V7},{fm.V8},{fm.V9},{fm.V10},{fm.V11},"
            f"{fm.complexity_score()},{fm.vulnerability_score()}"
        )
    print(f"len(all_metrics){len(all_metrics)}")


if __name__ == "__main__":
    main()

