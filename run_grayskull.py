"""Touch up the conda recipe from grayskull using conda-souschef."""
import os
from os.path import join

from souschef.recipe import Recipe

import ElM2D

os.system("grayskull pypi ElM2D=={}".format(ElM2D.__version__))

fpath = join("chem_wasserstein", "meta.yaml")
fpath2 = join("scratch", "meta.yaml")
my_recipe = Recipe(load_file=fpath)
my_recipe["requirements"]["host"].append("flit")
my_recipe.save(fpath)
my_recipe.save(fpath2)

# follow with "cd scratch; conda build ."
