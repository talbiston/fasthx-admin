"""
Setuptools build for fasthx-admin.

Package metadata lives here rather than in a pyproject.toml ``[project]`` table
because the GTT Jenkins pipeline (edge-jenkins-lib ``pushPyPackage``) rewrites
the version line below from the git tag before building the sdist.
"""

from os import path

from setuptools import find_packages, setup

here = path.abspath(path.dirname(__file__))

with open(path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = f.read()


setup(
    name="fasthx-admin",
    version="0.6.4",
    description=(
        "FastAPI + HTMX + Jinja2 admin interface framework "
        "— a modern replacement for Flask-Admin"
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/talbiston/fasthx-admin",
    author="talbiston",
    license="MIT",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Framework :: FastAPI",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
    ],
    keywords="fastapi htmx admin crud jinja2",

    package_dir={"": "src"},
    packages=find_packages(where="src"),
    include_package_data=True,
    python_requires=">=3.9",
    install_requires=[
        "fastapi",
        "sqlalchemy",
        "jinja2",
        "python-multipart",
        "requests",
        "itsdangerous",
        "celery",
    ],
    extras_require={
        "ai": ["httpx"],
        "xlsx": ["openpyxl"],
        "dev": ["uvicorn[standard]", "pytest", "httpx"],
    },

    project_urls={
        "Source Code": "https://github.com/talbiston/fasthx-admin",
    },
)
