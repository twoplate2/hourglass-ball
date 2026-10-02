"""p4a recipe: 把 flowcore(纯 C 扩展)编进 APK。

- 源码放本 recipe 的 src/(IncludedFilesBehaviour 会拷进构建目录)。
- should_build 必须返回 True: PythonRecipe 默认按"site-packages 里有没有这个包"判断,
  CI 命中 buildozer 缓存后会**永远不再重编**, 拿旧 .so 跑一整天。
- prebuild_arch 里给 hostpython 装 setuptools: p4a 的 build_compiled_components 是用
  **hostpython** 跑 `setup.py build_ext`(recipe.py:990), 而 CI 的 host 环境里可能没有
  setuptools —— 实测报错就是 `ModuleNotFoundError: No module named 'setuptools'`。
  只声明 depends=['setuptools'] 不够(那是目标侧 recipe, 不装进 host 环境)。
"""
import sys

from pythonforandroid.logger import shprint
from pythonforandroid.recipe import CompiledComponentsPythonRecipe, IncludedFilesBehaviour
from pythonforandroid.util import current_directory
import sh


class FlowcoreRecipe(IncludedFilesBehaviour, CompiledComponentsPythonRecipe):
    version = "1.0"
    url = None
    src_filename = "src"
    depends = ["setuptools"]
    build_cmd = "build_ext"

    def should_build(self, arch):
        return True

    def prebuild_arch(self, arch):
        super().prebuild_arch(arch)
        # 用当前进程的解释器(p4a 自己跑在这个 Python 上), 不能取 ctx.hostpython ——
        # prebuild 阶段它还没赋值, 实测报 AttributeError: 'Context' object has no
        # attribute 'hostpython'。
        hostpython = sh.Command(sys.executable)
        with current_directory(self.get_build_dir(arch.arch)):
            shprint(hostpython, "-m", "pip", "install", "--quiet", "setuptools")


recipe = FlowcoreRecipe()
