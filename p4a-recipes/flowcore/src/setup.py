from setuptools import Extension, setup

setup(name="flowcore", version="1.0",
      ext_modules=[Extension("flowcore", sources=["flowcore.c"])])
