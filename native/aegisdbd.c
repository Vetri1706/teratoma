#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#ifndef AEGIS_PYTHON_HOME
#error "Build with scripts/build_native.py to configure the embedded Python home"
#endif

static int repository_root(char *root, size_t capacity) {
    char executable[PATH_MAX];
#ifdef __APPLE__
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) return -1;
#else
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || (size_t)length >= sizeof(executable) - 1) return -1;
    executable[length] = '\0';
#endif
    char canonical[PATH_MAX];
    if (realpath(executable, canonical) == NULL) { perror("realpath executable"); return -1; }
    char *separator = strrchr(canonical, '/');
    if (separator == NULL) return -1;
    *separator = '\0';
    separator = strrchr(canonical, '/');
    if (separator == NULL) return -1;
    *separator = '\0';
    if (strlen(canonical) + 1 > capacity) return -1;
    memcpy(root, canonical, strlen(canonical) + 1);
    char module[PATH_MAX];
    int written = snprintf(module, sizeof(module), "%s/src/production/daemon.py", root);
    if (written < 0 || (size_t)written >= sizeof(module) || access(module, R_OK) != 0) {
        fprintf(stderr, "Cannot find src/production/daemon.py beside bin: %s\n", root);
        return -1;
    }
    return 0;
}

int main(int argc, char **argv) {
    char root[PATH_MAX];
    if (repository_root(root, sizeof(root)) != 0) return 2;
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.parse_argv = 0;
    config.site_import = 0;
    config.write_bytecode = 0;
    PyStatus status = PyConfig_SetBytesString(&config, &config.home, AEGIS_PYTHON_HOME);
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesString(&config, &config.program_name, argv[0]);
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    int exit_code = 0;
    PyObject *path = PySys_GetObject("path");
    PyObject *directory = PyUnicode_DecodeFSDefault(root);
    if (path == NULL || directory == NULL || PyList_Insert(path, 0, directory) != 0) {
        Py_XDECREF(directory); PyErr_Print(); exit_code = 1;
    } else {
        Py_DECREF(directory);
        PyObject *module = PyImport_ImportModule("src.production.daemon");
        PyObject *entry = module == NULL ? NULL : PyObject_GetAttrString(module, "main");
        PyObject *result = entry == NULL ? NULL : PyObject_CallNoArgs(entry);
        if (result == NULL) { PyErr_Print(); exit_code = 1; }
        else if (PyLong_Check(result)) {
            long value = PyLong_AsLong(result);
            if (PyErr_Occurred()) { PyErr_Print(); exit_code = 1; }
            else exit_code = value >= 0 && value <= 255 ? (int)value : 1;
        }
        Py_XDECREF(result); Py_XDECREF(entry); Py_XDECREF(module);
    }
    if (Py_FinalizeEx() < 0 && exit_code == 0) exit_code = 120;
    return exit_code;
}
