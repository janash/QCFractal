# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# Import the package for version info
import qcfractal

# Other packages
import os
import datetime

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))


# -- Project information -----------------------------------------------------

project = 'QCArchive'
copyright = f'2018-{datetime.datetime.today().year}, The Molecular Sciences Software Institute'
author = 'Molecular Sciences Software Institute'

# The short X.Y version
version = qcfractal.__version__
# The full version, including alpha/beta/rc tags
release = qcfractal.__version__


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
#    'nbsphinx',
    'sphinx_design',
#    'sphinx.ext.mathjax',
#    'sphinx.ext.extlinks',
#    'sphinx.ext.doctest',
#    'sphinx.ext.todo',
#    'sphinx.ext.coverage',
#    'sphinx.ext.intersphinx',
#    'sphinx_automodapi.automodapi',
]

# Some options
highlight_language = 'python3'
add_module_names = False
autoclass_content = "both"
autodoc_typehints = 'description'
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'inherited-members': True,
    'show-inheritance': True,
    'member-order': 'bysource',
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates', ]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ['.ipynb_checkpoints/*']


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = 'pydata_sphinx_theme'

html_theme_options = {
	"github_url": "https://github.com/MolSSI/QCFractal",

	"logo": {
      "image_light": "molssi_main_logo.png",
      "image_dark": "molssi_main_logo_inverted_white.png",
    },
	"show_toc_level": 2,
	"header_links_before_dropdown": 4,
	"external_links": [
      {"name": "MolSSI", "url": "https://molssi.org"}
  ],

	"secondary_sidebar_items": ["page-toc", "sourcelink"],
}

html_css_files = ['css/custom.css']


# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']