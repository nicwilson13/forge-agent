from setuptools import setup, find_packages

setup(
    name="forge-agent",
    version="0.1.0",
    description="Autonomous AI development agent powered by Claude Code",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "anthropic>=0.40.0",
    ],
    entry_points={
        "console_scripts": [
            "forge=forge.cli:main",
        ],
    },
)
