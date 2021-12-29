import ast
import sys


DECISION_POINTS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def iterate(syntax):
    # print(file_lines[syntax.lineno -1])
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

def print_source_line(source, line_no):
    print(source[line_no - 1])


if __name__ == '__main__':

    # parse argv
    assert len(sys.argv) == 3, 'Usage: "python ast_parser.py <filename> <thold>"'
    script, file, thold_str = sys.argv

    thold = int(thold_str)
    # currently thold is just an int applied to everything. it is probably ok, for classes to have different thold

    # read file
    with open(file, 'r') as f:
        file_text = f.read()
        file_lines = file_text.splitlines()

    a = ast.parse(file_text)

    source_code_to_analyze = [f for f in a.body if isinstance(f, DECISION_POINTS)]

    # start iterating over syntax
    for syntax in source_code_to_analyze:
        score = iterate(syntax)
        if score >= thold:
            print_source_line(file_lines, syntax.lineno)
            print(score)

        if isinstance(syntax, ast.ClassDef):
            for method in [f for f in syntax.body if isinstance(f, DECISION_POINTS)]:
                # for classes we also iterate each method
                score = iterate(method)
                if score >= thold:
                    print_source_line(file_lines, method.lineno)
                    print(score)
