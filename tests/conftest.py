"""
Shared pytest fixtures — creates temporary Python files with
intentional code smells for testing the analyzers.
"""

import os
import textwrap

import pytest


@pytest.fixture
def sample_repo(tmp_path):
    """
    Create a temporary directory containing Python files with deliberate
    issues: high complexity, lint errors, dead code, and low maintainability.
    """
    # --- File 1: High complexity ---
    complex_file = tmp_path / "complex_module.py"
    complex_file.write_text(textwrap.dedent("""
        import os
        import sys

        def very_complex_function(a, b, c, d, e, f):
            '''A deliberately complex function for testing.'''
            result = 0
            if a > 0:
                if b > 0:
                    if c > 0:
                        result = a + b + c
                    else:
                        result = a + b - c
                elif d > 0:
                    if e > 0:
                        result = d + e
                    elif f > 0:
                        result = d + f
                    else:
                        result = d
                else:
                    result = a
            elif b > 0:
                if c > 0:
                    result = b + c
                elif d > 0:
                    result = b + d
                else:
                    result = b
            elif c > 0:
                if d > 0:
                    if e > 0:
                        result = c + d + e
                    else:
                        result = c + d
                elif f > 0:
                    result = c + f
                else:
                    result = c
            else:
                result = -1
            return result

        def another_complex(x, y, z):
            if x == 1:
                if y == 1:
                    return x + y
                elif y == 2:
                    return x - y
                elif y == 3:
                    if z > 0:
                        return z
                    else:
                        return -z
                else:
                    return 0
            elif x == 2:
                if z == 1:
                    return z + x
                elif z == 2:
                    return z - x
                else:
                    return x
            elif x == 3:
                return x * y * z
            else:
                return -1


        def unused_helper_function():
            '''This function is never called - dead code.'''
            return 42


        UNUSED_CONSTANT = 999

        class UnusedClass:
            '''This class is never instantiated - dead code.'''
            def method(self):
                pass
    """), encoding="utf-8")

    # --- File 2: Lint issues ---
    lint_file = tmp_path / "lint_issues.py"
    lint_file.write_text(textwrap.dedent("""
        import os
        import json
        import sys
        import re

        x=1
        y =2
        z= 3

        def bad_style( a,b ,c):
            D = a+b+c
            return D

        def no_return_type(value):
            if value:
                print(value)

        class myclass:
            def Method(self):
                pass
    """), encoding="utf-8")

    # --- File 3: More dead code ---
    dead_file = tmp_path / "dead_code.py"
    dead_file.write_text(textwrap.dedent("""
        def used_function():
            return "I am used"

        def orphan_function_a():
            return "I am never called"

        def orphan_function_b():
            return "I am also never called"

        ORPHAN_VAR = "never read"

        class OrphanClass:
            def orphan_method(self):
                pass

        if __name__ == "__main__":
            used_function()
    """), encoding="utf-8")

    return tmp_path


@pytest.fixture
def sample_repo_str(sample_repo):
    """Return the sample repo path as a string."""
    return str(sample_repo)
