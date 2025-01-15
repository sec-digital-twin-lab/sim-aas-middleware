#!/usr/bin/env python
import os

from setuptools import setup, find_packages

package_name = 'simaas'

# read meta information without importing
_locals = {}
with open(os.path.join(package_name, "meta.py")) as f:
    exec(f.read(), None, _locals)

# read the long description
with open('README.md') as f:
    long_description = f.read()

# read the requirements
with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name=package_name,
    version=_locals["__version__"],
    install_requires=requirements,
    extras_require={
        'dev': [
            'pytest',
            'ruff'
        ]
    },
    packages=find_packages(),
    include_package_data=True,
    url='https://github.com/sec-digital-twin-lab/sim-aas-middleware',
    project_urls={
        'Source': 'https://github.com/sec-digital-twin-lab/sim-aas-middleware',
        'Tracker': 'https://github.com/sec-digital-twin-lab/sim-aas-middleware/issues',
    },
    license='MIT',
    description=_locals["__description__"],
    long_description=long_description,
    long_description_content_type='text/markdown',
    entry_points={
        'console_scripts': [
            'simaas-cli = simaas.cli.saas_cli:main',
            'simaas-node = simaas.service:main'
        ]
    },
    classifiers=[
        'Programming Language :: Python :: 3'
        'Operating System :: OS Independent'
    ],
)
