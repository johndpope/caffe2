import contextlib
from google.protobuf.message import Message
from multiprocessing import Process
import os
import shutil
import socket
import tempfile
import logging

import numpy as np
from caffe2.proto import caffe2_pb2
from caffe2.python import scope, utils

import caffe2.python._import_c_extension as C

logger = logging.getLogger(__name__)

Blobs = C.blobs
CreateBlob = C.create_blob
CurrentWorkspace = C.current_workspace
DeserializeBlob = C.deserialize_blob
GlobalInit = C.global_init
HasBlob = C.has_blob
RegisteredOperators = C.registered_operators
SerializeBlob = C.serialize_blob
SwitchWorkspace = C.switch_workspace
RootFolder = C.root_folder
Workspaces = C.workspaces
BenchmarkNet = C.benchmark_net

is_asan = C.is_asan
has_gpu_support = C.has_gpu_support
if has_gpu_support:
    NumCudaDevices = C.num_cuda_devices
    SetDefaultGPUID = C.set_default_gpu_id
    GetDefaultGPUID = C.get_default_gpu_id
    GetCuDNNVersion = C.get_cudnn_version

    def GetCudaPeerAccessPattern():
        return np.asarray(C.get_cuda_peer_access_pattern())
else:
    NumCudaDevices = lambda: 0 # noqa
    SetDefaultGPUID = lambda x: None # noqa
    GetDefaultGPUID = lambda: 0 # noqa
    GetCuDNNVersion = lambda: 0 # noqa
    GetCudaPeerAccessPattern = lambda: np.array([]) # noqa


# Python 2 and 3 compatibility: test if basestring exists
try:
    basestring  # NOQA
except NameError:
    # This is python3 so we define basestring.
    basestring = str


def _GetFreeFlaskPort():
    """Get a free flask port."""
    # We will prefer to use 5000. If not, we will then pick a random port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 5000))
    if result == 0:
        return 5000
    else:
        s = socket.socket()
        s.bind(('', 0))
        port = s.getsockname()[1]
        s.close()
        # Race condition: between the interval we close the socket and actually
        # start a mint process, another process might have occupied the port. We
        # don't do much here as this is mostly for convenience in research
        # rather than 24x7 service.
        return port


def StartMint(root_folder=None, port=None):
    """Start a mint instance.

    TODO(Yangqing): this does not work well under ipython yet. According to
        https://github.com/ipython/ipython/issues/5862
    writing up some fix is a todo item.
    """
    from caffe2.python.mint import app
    if root_folder is None:
        # Get the root folder from the current workspace
        root_folder = C.root_folder()
    if port is None:
        port = _GetFreeFlaskPort()
    process = Process(
        target=app.main,
        args=(
            ['-p', str(port), '-r', root_folder],
        )
    )
    process.start()
    print('Mint running at http://{}:{}'.format(socket.getfqdn(), port))
    return process


def StringifyProto(obj):
    """Stringify a protocol buffer object.

  Inputs:
    obj: a protocol buffer object, or a Pycaffe2 object that has a Proto()
        function.
  Outputs:
    string: the output protobuf string.
  Raises:
    AttributeError: if the passed in object does not have the right attribute.
  """
    if type(obj) is str:
        return obj
    else:
        if isinstance(obj, Message):
            # First, see if this object is a protocol buffer, which we can
            # simply serialize with the SerializeToString() call.
            return obj.SerializeToString()
        elif hasattr(obj, 'Proto'):
            return obj.Proto().SerializeToString()
        else:
            raise ValueError("Unexpected argument to StringifyProto of type " +
                             type(obj).__name__)


def ResetWorkspace(root_folder=None):
    if root_folder is None:
        # Reset the workspace, but keep the current root folder setting.
        return C.reset_workspace(C.root_folder())
    else:
        if not os.path.exists(root_folder):
            os.makedirs(root_folder)
        return C.reset_workspace(root_folder)


def CreateNet(net, input_blobs=None):
    if input_blobs is None:
        input_blobs = []
    for input_blob in input_blobs:
        C.create_blob(input_blob)
    return C.create_net(StringifyProto(net))


def RunOperatorOnce(operator):
    return C.run_operator_once(StringifyProto(operator))


def RunOperatorsOnce(operators):
    for op in operators:
        success = RunOperatorOnce(op)
        if not success:
            return False
    return True


def RunNetOnce(net):
    return C.run_net_once(StringifyProto(net))


def RunNet(name, num_iter=1):
    """Runs a given net.

    Inputs:
      name: the name of the net, or a reference to the net.
      num_iter: number of iterations to run
    Returns:
      True or an exception.
    """
    return C.run_net(StringifyNetName(name), num_iter)


def RunPlan(plan_or_step):
    # TODO(jiayq): refactor core.py/workspace.py to avoid circular deps
    import caffe2.python.core as core
    if isinstance(plan_or_step, core.ExecutionStep):
        plan_or_step = core.Plan(plan_or_step)
    return C.run_plan(StringifyProto(plan_or_step))


def InferShapesAndTypes(nets, blob_dimensions=None):
    """Infers the shapes and types for the specified nets.

    Inputs:
      nets: the list of nets
      blob_dimensions (optional): a dictionary of blobs and their dimensions.
          If not specified, the workspace blobs are used.
    Returns:
      A tuple of (shapes, types) dictionaries keyed by blob name.
    """
    net_protos = [StringifyProto(n.Proto()) for n in nets]
    if blob_dimensions is None:
        blobdesc_prototxt = C.infer_shapes_and_types_from_workspace(net_protos)
    else:
        blobdesc_prototxt = C.infer_shapes_and_types_from_map(
            net_protos, blob_dimensions
        )
    blobdesc_proto = caffe2_pb2.TensorShapes()
    blobdesc_proto.ParseFromString(blobdesc_prototxt)
    shapes = {}
    types = {}
    for ts in blobdesc_proto.shapes:
        if not ts.unknown_shape:
            shapes[ts.name] = list(ts.dims)
            types[ts.name] = ts.data_type

    return (shapes, types)


def _StringifyName(name, expected_type):
    if isinstance(name, basestring):
        return name
    assert type(name).__name__ == expected_type, \
        "Expected a string or %s" % expected_type
    return str(name)


def StringifyBlobName(name):
    return _StringifyName(name, "BlobReference")


def StringifyNetName(name):
    return _StringifyName(name, "Net")


def FeedBlob(name, arr, device_option=None):
    """Feeds a blob into the workspace.

    Inputs:
      name: the name of the blob.
      arr: either a TensorProto object or a numpy array object to be fed into
          the workspace.
      device_option (optional): the device option to feed the data with.
    Returns:
      True or False, stating whether the feed is successful.
    """
    if type(arr) is caffe2_pb2.TensorProto:
        arr = utils.Caffe2TensorToNumpyArray(arr)
    if type(arr) is np.ndarray and arr.dtype.kind == 'S':
        # Plain NumPy strings are weird, let's use objects instead
        arr = arr.astype(np.object)

    if device_option is None:
        device_option = scope.CurrentDeviceScope()

    if device_option and device_option.device_type == caffe2_pb2.CUDA:
        if arr.dtype == np.dtype('float64'):
            logger.warning(
                "CUDA operators do not support 64-bit doubles, " +
                "please use arr.astype(np.float32) or np.int32 for ints." +
                " Blob: {}".format(name) +
                " type: {}".format(str(arr.dtype))
            )

    name = StringifyBlobName(name)
    if device_option is not None:
        return C.feed_blob(name, arr, StringifyProto(device_option))
    else:
        return C.feed_blob(name, arr)


def FetchBlobs(names):
    """Fetches a list of blobs from the workspace.

    Inputs:
        names: list of names of blobs - strings or BlobReferences
    Returns:
        list of fetched blobs
    """
    return [FetchBlob(name) for name in names]


def FetchBlob(name):
    """Fetches a blob from the workspace.

    Inputs:
      name: the name of the blob - a string or a BlobReference
    Returns:
      Fetched blob (numpy array or string) if successful
    """
    return C.fetch_blob(StringifyBlobName(name))


def GetNameScope():
    """Return the current namescope string. To be used to fetch blobs"""
    return scope.CurrentNameScope()


class _BlobDict(object):
    """Provides python dict compatible way to do fetching and feeding"""

    def __getitem__(self, key):
        return FetchBlob(key)

    def __setitem__(self, key, value):
        return FeedBlob(key, value)

    def __len__(self):
        return len(C.blobs())

    def __iter__(self):
        return C.blobs().__iter__()

    def __contains__(self, item):
        return C.has_blob(item)


blobs = _BlobDict()


################################################################################
# Utilities for immediate mode
#
# Caffe2's immediate mode implements the following behavior: between the two
# function calls StartImmediate() and StopImmediate(), for any operator that is
# called through CreateOperator(), we will also run that operator in a workspace
# that is specific to the immediate mode. The user is explicitly expected to
# make sure that these ops have proper inputs and outputs, i.e. one should not
# run an op where an external input is not created or fed.
#
# Users can use FeedImmediate() and FetchImmediate() to interact with blobs
# in the immediate workspace.
#
# Once StopImmediate() is called, all contents in the immediate workspace is
# freed up so one can continue using normal runs.
#
# The immediate mode is solely for debugging purposes and support will be very
# sparse.
################################################################################

_immediate_mode = False
_immediate_workspace_name = "_CAFFE2_IMMEDIATE"
_immediate_root_folder = ''


def IsImmediate():
    return _immediate_mode


@contextlib.contextmanager
def WorkspaceGuard(workspace_name):
    current = CurrentWorkspace()
    SwitchWorkspace(workspace_name, True)
    yield
    SwitchWorkspace(current)


def StartImmediate(i_know=False):
    global _immediate_mode
    global _immediate_root_folder
    if IsImmediate():
        # already in immediate mode. We will kill the previous one
        # and start from fresh.
        StopImmediate()
    _immediate_mode = True
    with WorkspaceGuard(_immediate_workspace_name):
        _immediate_root_folder = tempfile.mkdtemp()
        ResetWorkspace(_immediate_root_folder)
    if i_know:
        # if the user doesn't want to see the warning message, sure...
        return
    print("""
    Enabling immediate mode in caffe2 python is an EXTREMELY EXPERIMENTAL
    feature and may very easily go wrong. This is because Caffe2 uses a
    declarative way of defining operators and models, which is essentially
    not meant to run things in an interactive way. Read the following carefully
    to make sure that you understand the caveats.

    (1) You need to make sure that the sequences of operators you create are
    actually runnable sequentially. For example, if you create an op that takes
    an input X, somewhere earlier you should have already created X.

    (2) Caffe2 immediate uses one single workspace, so if the set of operators
    you run are intended to be under different workspaces, they will not run.
    To create boundaries between such use cases, you can call FinishImmediate()
    and StartImmediate() manually to flush out everything no longer needed.

    (3) Underlying objects held by the immediate mode may interfere with your
    normal run. For example, if there is a leveldb that you opened in immediate
    mode and did not close, your main run will fail because leveldb does not
    support double opening. Immediate mode may also occupy a lot of memory esp.
    on GPUs. Call FinishImmediate() as soon as possible when you no longer
    need it.

    (4) Immediate is designed to be slow. Every immediate call implicitly
    creates a temp operator object, runs it, and destroys the operator. This
    slow-speed run is by design to discourage abuse. For most use cases other
    than debugging, do NOT turn on immediate mode.

    (5) If there is anything FATAL happening in the underlying C++ code, the
    immediate mode will immediately (pun intended) cause the runtime to crash.

    Thus you should use immediate mode with extra care. If you still would
    like to, have fun [https://xkcd.com/149/].
    """)


def StopImmediate():
    """Stops an immediate mode run."""
    # Phew, that was a dangerous ride.
    global _immediate_mode
    global _immediate_root_folder
    if not IsImmediate():
        return
    with WorkspaceGuard(_immediate_workspace_name):
        ResetWorkspace()
    shutil.rmtree(_immediate_root_folder)
    _immediate_root_folder = ''
    _immediate_mode = False


def ImmediateBlobs():
    with WorkspaceGuard(_immediate_workspace_name):
        return Blobs()


def RunOperatorImmediate(op):
    with WorkspaceGuard(_immediate_workspace_name):
        RunOperatorOnce(op)


def FetchImmediate(*args, **kwargs):
    with WorkspaceGuard(_immediate_workspace_name):
        return FetchBlob(*args, **kwargs)


def FeedImmediate(*args, **kwargs):
    with WorkspaceGuard(_immediate_workspace_name):
        return FeedBlob(*args, **kwargs)


# CWorkspace utilities

def _Workspace_create_net(ws, net):
    return ws._create_net(StringifyProto(net))


C.Workspace.create_net = _Workspace_create_net


def _Workspace_run(ws, obj):
    if hasattr(obj, 'Proto'):
        obj = obj.Proto()
    if isinstance(obj, caffe2_pb2.PlanDef):
        return ws._run_plan(obj.SerializeToString())
    if isinstance(obj, caffe2_pb2.NetDef):
        return ws._run_net(obj.SerializeToString())
    if isinstance(obj, caffe2_pb2.OperatorDef):
        return ws._run_operator(obj.SerializeToString())
    raise ValueError(
        "Don't know how to do Workspace.run() on {}".format(type(obj)))


C.Workspace.run = _Workspace_run


def _Blob_feed(blob, arg, device_option=None):
    if device_option is not None:
        device_option = StringifyProto(device_option)
    return blob._feed(arg, device_option)


C.Blob.feed = _Blob_feed
