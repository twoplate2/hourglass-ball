from pythonforandroid.recipe import CompiledComponentsPythonRecipe


class PymunkRecipe(CompiledComponentsPythonRecipe):
    name = "pymunk"
    version = "7.3.0"
    url = "https://files.pythonhosted.org/packages/source/p/pymunk/pymunk-{version}.tar.gz"
    depends = ["cffi", "setuptools"]
    call_hostpython_via_targetpython = False

    def get_recipe_env(self, arch):
        env = super().get_recipe_env(arch)
        env["LDFLAGS"] += " -llog -lm"
        env["PYMUNK_BUILD_SLIM"] = "1"
        return env


recipe = PymunkRecipe()
