"""p4a recipe: 把 native 的 flowcore 编成 APK 内的扩展模块(纯 C, 无需 Cython)。

- 源码放本 recipe 的 src/(IncludedFilesBehaviour 会把它拷进构建目录)。
- should_build 必须返回 True: PythonRecipe 默认按"site-packages 里有没有这个包"判断,
  CI 命中 buildozer 缓存后会**永远不再重编**, 拿旧 .so 跑一整天。
"""
from pythonforandroid.recipe import CompiledComponentsPythonRecipe, IncludedFilesBehaviour


class FlowcoreRecipe(IncludedFilesBehaviour, CompiledComponentsPythonRecipe):
    version = "1.0"
    url = None
    src_filename = "src"
    depends = ["setuptools"]
    build_cmd = "build_ext"

    def should_build(self, arch):
        return True


recipe = FlowcoreRecipe()
