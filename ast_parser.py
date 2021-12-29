import ast
import sys

assert len(sys.argv) == 2, 'Usage: "python ast_parser.py <filename>"'
file = sys.argv[-1]


with open(file, 'r') as f:
    file_text = f.read()
    file_lines = file_text.splitlines()

a = ast.parse(file_text)

executable_logic_points = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

source_code_to_analyze = [f for f in a.body if isinstance(f, executable_logic_points)]

def iterate(syntax):
    # logic_points = [ast.IfExp]
    # print(file_lines[stmt.lineno -1])
    n_stmt = 1
    for maybe_stmt in syntax.body:
        if not isinstance(maybe_stmt, ast.stmt):
            continue

        if hasattr(maybe_stmt, 'body'):
            # stmt
            stmt = maybe_stmt
            n_stmt += iterate(stmt)
            continue

        if hasattr(maybe_stmt, 'value'):
            # assignment
            pass

        # instead of hasattr @Speed options (to test or consider)
        # 1) if not isinstance(ast.FunctionDef, ast.IfExpr, ..)
        # 2) try-except

    return n_stmt


for syntax in source_code_to_analyze:
    print(file_lines[syntax.lineno -1])
    print(iterate(syntax))
    if isinstance(syntax, ast.ClassDef):
        for method in [f for f in syntax.body if isinstance(f, executable_logic_points)]:
            # for classes we also iterate each method
            print(file_lines[method.lineno -1])
            print(iterate(method))
