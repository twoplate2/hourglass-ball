/* flowcore.c —— 沙流每帧热点的原生实现。
 *
 * 约束(与 tools/flow_texture_experiment.py 的 Python 版**逐位等价**):
 *   bottom = p["y"]
 *   vy     = |p["vy"]|
 *   trail  = max(2.0, vy * p["trail_time"] / motion_scale)
 *   top    = min(bottom + trail, top_limit)
 *   依次把 x / bottom / top 作为 little-endian float32 写进 data(每颗粒 12 字节)
 * 浮点一律用 double 算、最后才转 float, 与 Python 版的 double 运算同序。
 *
 * 装载方式是可选加速: 拿不到就退回 Python 原实现(main.py 侧做 fallback)。
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>

/* 键只哈希一次并常驻: 每颗粒 4 次 PyDict_GetItemString 会重新哈希 C 字符串,
 * 实测这是"字典版 C 化"只有 2.2 倍的主要原因。 */
static PyObject *key_x, *key_y, *key_vy, *key_trail;

static int cache_keys(void)
{
    if (key_x != NULL)
        return 0;
    key_x = PyUnicode_InternFromString("x");
    key_y = PyUnicode_InternFromString("y");
    key_vy = PyUnicode_InternFromString("vy");
    key_trail = PyUnicode_InternFromString("trail_time");
    return (key_x && key_y && key_vy && key_trail) ? 0 : -1;
}

static PyObject *pack_stream(PyObject *self, PyObject *args)
{
    PyObject *particles, *data;
    double top_limit, motion_scale;
    Py_ssize_t start = 0, count = -1;
    if (!PyArg_ParseTuple(args, "OOdd|nn", &particles, &data, &top_limit,
                          &motion_scale, &start, &count))
        return NULL;
    if (cache_keys() != 0)
        return NULL;
    if (!PyByteArray_Check(data)) {
        PyErr_SetString(PyExc_TypeError, "data must be a bytearray");
        return NULL;
    }
    PyObject *fast = PySequence_Fast(particles, "particles must be a sequence");
    if (fast == NULL)
        return NULL;
    const Py_ssize_t n = PySequence_Fast_GET_SIZE(fast);
    if (start < 0)
        start = 0;
    if (count < 0 || start + count > n)
        count = n - start;
    if (count < 0)
        count = 0;
    char *buf = PyByteArray_AS_STRING(data) + start * 12;   /* 与 Python 同一下标语义 */
    const Py_ssize_t cap = PyByteArray_GET_SIZE(data);
    if (cap < (start + count) * 12) {
        Py_DECREF(fast);
        PyErr_SetString(PyExc_ValueError, "data buffer too small");
        return NULL;
    }
    PyObject **items = PySequence_Fast_ITEMS(fast);
    for (Py_ssize_t i = start; i < start + count; i++) {
        PyObject *p = items[i];
        PyObject *ox = PyDict_GetItem(p, key_x);
        PyObject *oy = PyDict_GetItem(p, key_y);
        PyObject *ovy = PyDict_GetItem(p, key_vy);
        PyObject *ot = PyDict_GetItem(p, key_trail);
        if (ox == NULL || oy == NULL || ovy == NULL || ot == NULL) {
            Py_DECREF(fast);
            PyErr_SetString(PyExc_KeyError, "particle missing x/y/vy/trail_time");
            return NULL;
        }
        const double x = PyFloat_AsDouble(ox);
        const double bottom = PyFloat_AsDouble(oy);
        double vy = PyFloat_AsDouble(ovy);
        if (vy < 0.0)
            vy = -vy;
        double trail = vy * PyFloat_AsDouble(ot) / motion_scale;
        if (trail < 2.0)
            trail = 2.0;
        double top = bottom + trail;
        if (top > top_limit)
            top = top_limit;
        const float fx = (float)x, fb = (float)bottom, ft = (float)top;
        memcpy(buf, &fx, 4);
        memcpy(buf + 4, &fb, 4);
        memcpy(buf + 8, &ft, 4);
        buf += 12;
    }
    Py_DECREF(fast);
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"pack_stream", pack_stream, METH_VARARGS,
     "pack_stream(particles, data, top_limit, motion_scale) -> None"},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "flowcore",
    "Native hot loops for the hourglass flow renderer.", -1, methods,
};

PyMODINIT_FUNC PyInit_flowcore(void)
{
    return PyModule_Create(&module);
}
