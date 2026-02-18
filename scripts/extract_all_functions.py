#!/usr/bin/env python3
"""
Extract all function and method definitions from Python files in the simaas codebase.
Outputs a JSON file with detailed information for each function.

Usage:
    python scripts/extract_all_functions.py > function_inventory.json
"""

import ast
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional


class FunctionExtractor(ast.NodeVisitor):
    """AST visitor that extracts function/method information."""

    def __init__(self, filepath: str, source_lines: List[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.functions: List[Dict[str, Any]] = []
        self.class_stack: List[str] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._extract_function(node, is_async=False)
        # Visit nested functions
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._extract_function(node, is_async=True)
        # Visit nested functions
        self.generic_visit(node)

    def _extract_function(self, node, is_async: bool):
        # Get class context
        class_name = self.class_stack[-1] if self.class_stack else None

        # Get decorators
        decorators = []
        for d in node.decorator_list:
            try:
                decorators.append(ast.unparse(d))
            except Exception:
                decorators.append("<unparseable>")

        # Analyze exception handling
        try_except_info = self._analyze_exception_handling(node)

        # Get function source (first few lines for context)
        start_line = node.lineno
        end_line = getattr(node, 'end_lineno', start_line + 10)

        # Get docstring if present
        docstring = ast.get_docstring(node)

        # Get arguments
        args = []
        for arg in node.args.args:
            args.append(arg.arg)

        # Determine if it's a method type
        method_type = None
        if class_name:
            if any(d in ['staticmethod', 'classmethod'] for d in decorators):
                method_type = 'staticmethod' if 'staticmethod' in decorators else 'classmethod'
            elif args and args[0] == 'self':
                method_type = 'instance'
            elif args and args[0] == 'cls':
                method_type = 'classmethod'

        self.functions.append({
            'file': self.filepath,
            'class': class_name,
            'name': node.name,
            'full_name': f"{class_name}.{node.name}" if class_name else node.name,
            'line_start': start_line,
            'line_end': end_line,
            'is_async': is_async,
            'is_private': node.name.startswith('_') and not node.name.startswith('__'),
            'is_dunder': node.name.startswith('__') and node.name.endswith('__'),
            'method_type': method_type,
            'decorators': decorators,
            'args': args,
            'has_docstring': docstring is not None,
            'exception_handling': try_except_info,
        })

    def _analyze_exception_handling(self, node) -> Dict[str, Any]:
        """Analyze all try/except blocks in a function."""
        try_blocks = []

        for child in ast.walk(node):
            if isinstance(child, ast.Try):
                handlers = []
                for handler in child.handlers:
                    handler_info = {
                        'line': handler.lineno,
                        'types': [],
                        'is_bare': handler.type is None,
                        'catches_exception': False,
                        'catches_base_exception': False,
                    }

                    if handler.type is None:
                        handler_info['types'].append('bare except')
                        handler_info['is_bare'] = True
                    elif isinstance(handler.type, ast.Name):
                        handler_info['types'].append(handler.type.id)
                        if handler.type.id == 'Exception':
                            handler_info['catches_exception'] = True
                        if handler.type.id == 'BaseException':
                            handler_info['catches_base_exception'] = True
                    elif isinstance(handler.type, ast.Tuple):
                        for elt in handler.type.elts:
                            if isinstance(elt, ast.Name):
                                handler_info['types'].append(elt.id)
                                if elt.id == 'Exception':
                                    handler_info['catches_exception'] = True
                                if elt.id == 'BaseException':
                                    handler_info['catches_base_exception'] = True

                    handlers.append(handler_info)

                try_blocks.append({
                    'line': child.lineno,
                    'handlers': handlers,
                })

        # Summary flags
        has_try_except = len(try_blocks) > 0
        has_bare_except = any(
            h['is_bare']
            for block in try_blocks
            for h in block['handlers']
        )
        catches_exception = any(
            h['catches_exception']
            for block in try_blocks
            for h in block['handlers']
        )
        all_exception_types = list(set(
            t
            for block in try_blocks
            for h in block['handlers']
            for t in h['types']
        ))

        return {
            'has_try_except': has_try_except,
            'has_bare_except': has_bare_except,
            'catches_exception': catches_exception,
            'exception_types': all_exception_types,
            'try_blocks': try_blocks,
        }


def extract_functions_from_file(filepath: str) -> List[Dict[str, Any]]:
    """Extract all functions from a single Python file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
            source_lines = source.splitlines()

        tree = ast.parse(source, filename=filepath)
        extractor = FunctionExtractor(filepath, source_lines)
        extractor.visit(tree)
        return extractor.functions

    except SyntaxError as e:
        return [{'error': f'SyntaxError: {e}', 'file': filepath}]
    except Exception as e:
        return [{'error': f'{type(e).__name__}: {e}', 'file': filepath}]


def find_python_files(root_dir: str, exclude_patterns: List[str] = None) -> List[str]:
    """Find all Python files, excluding test files."""
    exclude_patterns = exclude_patterns or ['tests', '__pycache__', '.git']
    python_files = []

    for path in Path(root_dir).rglob('*.py'):
        # Check if any exclude pattern is in the path
        path_str = str(path)
        if any(excl in path_str for excl in exclude_patterns):
            continue
        python_files.append(path_str)

    return sorted(python_files)


def main():
    # Find the simaas directory
    script_dir = Path(__file__).parent.parent
    simaas_dir = script_dir / 'simaas'

    if not simaas_dir.exists():
        print(f"Error: simaas directory not found at {simaas_dir}", file=sys.stderr)
        sys.exit(1)

    # Find all Python files (excluding tests)
    python_files = find_python_files(str(simaas_dir), exclude_patterns=['tests', '__pycache__'])

    # Extract functions from all files
    all_functions = []
    files_processed = []

    for filepath in python_files:
        rel_path = os.path.relpath(filepath, script_dir)
        functions = extract_functions_from_file(filepath)

        # Update file paths to be relative
        for func in functions:
            func['file'] = rel_path

        all_functions.extend(functions)
        files_processed.append(rel_path)

    # Create output structure
    output = {
        'metadata': {
            'total_files': len(files_processed),
            'total_functions': len(all_functions),
            'files': files_processed,
        },
        'functions': all_functions,
        'summary': {
            'with_try_except': sum(1 for f in all_functions if f.get('exception_handling', {}).get('has_try_except')),
            'with_bare_except': sum(1 for f in all_functions if f.get('exception_handling', {}).get('has_bare_except')),
            'catching_exception': sum(1 for f in all_functions if f.get('exception_handling', {}).get('catches_exception')),
            'async_functions': sum(1 for f in all_functions if f.get('is_async')),
            'private_functions': sum(1 for f in all_functions if f.get('is_private')),
            'dunder_methods': sum(1 for f in all_functions if f.get('is_dunder')),
        }
    }

    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
