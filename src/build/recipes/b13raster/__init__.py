from pathlib import Path
import subprocess

from pythonforandroid.recipe import Recipe


class B13RasterRecipe(Recipe):
    name = "b13raster"
    version = "1"
    built_libraries = {"libb13raster.so": "."}

    def build_arch(self, arch):
        out = Path(self.get_build_dir(arch.arch))
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run([arch.clang_exe, "--target=" + arch.target, "-std=c99", "-O3",
                        "-fPIC", "-fno-math-errno", "-ffp-contract=off", "-shared",
                        "-Wl,-soname,libb13raster.so", "-Wl,-z,max-page-size=16384",
                        str(Path(__file__).with_name("raster.c")), "-o",
                        str(out / "libb13raster.so"), "-lm"], check=True)


recipe = B13RasterRecipe()
