from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from hypothesis import given
import hypothesis.strategies as st
import unittest

from caffe2.proto import caffe2_pb2
from caffe2.python import core, test_util
from caffe2.python.core import CreateOperator, GradientRegistry


# First, we will set up a few gradient registry entries so that we can manually
# construct some test cases.


def NeedAll(op, g_output):
    """A sanity check to make sure that all the gradient are given."""
    for name, g in zip(op.output, g_output):
        if g is None:
            raise RuntimeError(
                'Need gradient for "%s" but it is not provided.' % name)
    return g_output


def GIS(op):
    """A test util function to generate the gradient name for input."""
    return [s + '_grad' for s in op.input]


def CopyDeviceOption(op, src_op):
    if src_op.HasField('device_option'):
        op.device_option.CopyFrom(src_op.device_option)
    return op


# First gradient: (in -> out) leading to (out_grad -> in_grad)
@GradientRegistry.RegisterGradient('Direct')
def AddDirectGradient(op, g_output):
    return (
        CopyDeviceOption(
            CreateOperator('DirectGradient', NeedAll(op, g_output), GIS(op)),
            op),
        GIS(op)
    )


# Second gradient: (in -> out) leading to (out, out_grad -> in_grad)
@GradientRegistry.RegisterGradient('UseOutput')
def AddUseOutputGradient(op, g_output):
    return (
        CopyDeviceOption(
            CreateOperator(
                'UseOutputGradient',
                list(op.output) + NeedAll(op, g_output), GIS(op)),
            op),
        GIS(op)
    )


@GradientRegistry.RegisterGradient('UseInput')
def AddUseInputGradient(op, g_output):
    return (
        CopyDeviceOption(
            CreateOperator(
                'UseInputGradient',
                list(op.input) + NeedAll(op, g_output), GIS(op)),
            op),
        GIS(op)
    )


@GradientRegistry.RegisterGradient('Nogradient')
def AddNogradient(op, g_output):
    return (
        [],
        [None for s in op.input]
    )


class TestGradientCalculation(test_util.TestCase):
    @given(device_option=st.sampled_from([
        None,
        core.DeviceOption(caffe2_pb2.CUDA, 1)]))
    def testDirect(self, device_option):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('Direct', 'hidden', 'out'),
        ]
        if device_option:
            for op in operators:
                op.device_option.CopyFrom(device_option)
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'out_grad', 'hidden_grad'),
            CreateOperator('DirectGradient', 'hidden_grad', 'in_grad'),
        ]
        if device_option:
            for op in desired_grad_operators:
                op.device_option.CopyFrom(device_option)
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testDirectImplicitGradientSource(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('Direct', 'hidden', 'out'),
        ]
        desired_grad_operators = [
            CreateOperator(
                "ConstantFill", 'out', "out_autogen_grad", value=1.0),
            CreateOperator(
                'DirectGradient', 'out_autogen_grad', 'hidden_grad'),
            CreateOperator('DirectGradient', 'hidden_grad', 'in_grad'),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, ['out'])
        self.assertEqual(gradients, desired_grad_operators)

    def testDoesNotGenerateUnnecessaryGradients(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('Direct', 'hidden', 'out'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'hidden_grad', 'in_grad'),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'hidden': 'hidden_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testDirectButNoOutputGradientGiven(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('Direct', 'hidden', 'out'),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {})
        self.assertEqual(gradients, [])

    def testDirectInPlace(self):
        operators = [
            CreateOperator('Direct', 'in', 'in'),
            CreateOperator('Direct', 'in', 'out'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'out_grad', 'in_grad'),
            CreateOperator('DirectGradient', 'in_grad', 'in_grad'),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testUseOutput(self):
        operators = [
            CreateOperator('UseOutput', 'in', 'hidden'),
            CreateOperator('UseOutput', 'hidden', 'out'),
            CreateOperator('Direct', 'out', 'sink'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'sink_grad', 'out_grad'),
            CreateOperator(
                'UseOutputGradient',
                ['out', 'out_grad'], 'hidden_grad'
            ),
            CreateOperator(
                'UseOutputGradient',
                ['hidden', 'hidden_grad'], 'in_grad'
            ),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'sink': 'sink_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testUseOutputInPlace(self):
        operators = [
            CreateOperator('UseOutput', 'in', 'in'),
            CreateOperator('UseOutput', 'in', 'out'),
            CreateOperator('Direct', 'out', 'sink'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'sink_grad', 'out_grad'),
            CreateOperator(
                'UseOutputGradient',
                ['out', 'out_grad'], 'in_grad'
            ),
            CreateOperator(
                'UseOutputGradient',
                ['in', 'in_grad'], 'in_grad'
            ),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'sink': 'sink_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testUseOutputButOutputHasBeenChanged(self):
        operators = [
            CreateOperator('UseOutput', 'in', 'hidden'),
            # Note here: we overwrite hidden, but hidden will be needed by the
            # gradient calculation of the first operator, so the gradient
            # registry should return an error.
            CreateOperator('Direct', 'hidden', 'hidden'),
            CreateOperator('UseOutput', 'hidden', 'out'),
            CreateOperator('Direct', 'out', 'sink'),
        ]
        with self.assertRaises(RuntimeError):
            gradients, _ = GradientRegistry.GetBackwardPass(
                operators, {'sink': 'sink_grad'})

    def testUseInput(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('UseInput', 'hidden', 'out'),
            CreateOperator('Direct', 'out', 'sink'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'sink_grad', 'out_grad'),
            CreateOperator(
                'UseInputGradient',
                ['hidden', 'out_grad'], 'hidden_grad'
            ),
            CreateOperator(
                'DirectGradient',
                'hidden_grad', 'in_grad'
            ),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'sink': 'sink_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testUseInputButInputHasBeenChanged(self):
        """Test gradient for the following case:

        in -> out, with UseInput
        in -> in

        Since we overwrite in in op#1, but in will be needed by the gradient
        calculation of op#0, the gradient registry should raise an error.
        """
        operators = [
            CreateOperator('UseInput', 'in', 'out'),
            CreateOperator('Direct', 'in', 'in'),
        ]
        with self.assertRaises(RuntimeError):
            gradients, _ = GradientRegistry.GetBackwardPass(
                operators, {'out': 'out_grad'})

    @given(device_option=st.sampled_from([
        None,
        core.DeviceOption(caffe2_pb2.CUDA, 1)]))
    def testMultiUseInput(self, device_option):
        """Test gradient for the following case:

        in -> hidden1
        in -> hidden2
        hidden1, hidden2 -> out
        """
        operators = [
            CreateOperator('Direct', 'in', 'hidden1'),
            CreateOperator('Direct', 'in', 'hidden2'),
            CreateOperator('Direct', ['hidden1', 'hidden2'], 'out'),
        ]
        if device_option:
            for op in operators:
                op.device_option.CopyFrom(device_option)
        desired_grad_operators = [
            CreateOperator(
                'DirectGradient',
                'out_grad', ['hidden1_grad', 'hidden2_grad']
            ),
            CreateOperator(
                'DirectGradient',
                'hidden2_grad', '_in_grad_autosplit_0'
            ),
            CreateOperator(
                'DirectGradient',
                'hidden1_grad', '_in_grad_autosplit_1'
            ),
            CreateOperator(
                'Sum',
                ['_in_grad_autosplit_0', '_in_grad_autosplit_1'], 'in_grad'
            ),
        ]
        if device_option:
            for op in desired_grad_operators:
                op.device_option.CopyFrom(device_option)
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {"out": "out_grad"})
        self.assertEqual(gradients, desired_grad_operators)

    def testMultiUseInputButWithNoGradient(self):
        """Test gradient for the following case:

        in -> hidden1
        in -(no gradient)-> hidden2
        hidden1, hidden2 -> out
        """
        operators = [
            CreateOperator('Direct', 'in', 'hidden1'),
            CreateOperator('Nogradient', 'in', 'hidden2'),
            CreateOperator('Direct', ['hidden1', 'hidden2'], 'out'),
        ]
        desired_grad_operators = [
            CreateOperator(
                'DirectGradient',
                'out_grad', ['hidden1_grad', 'hidden2_grad']
            ),
            CreateOperator(
                'DirectGradient',
                'hidden1_grad', 'in_grad'
            ),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testMultiUseInputAndMultipleVersions(self):
        """Test gradient for the following case:

        in -> in
        in -> hidden1, hidden2
        hidden1, hidden2 -> out
        """
        operators = [
            CreateOperator('Direct', 'in', 'in'),
            CreateOperator('Direct', 'in', 'hidden1'),
            CreateOperator('Direct', 'in', 'hidden2'),
            CreateOperator('Direct', ['hidden1', 'hidden2'], 'out'),
        ]
        desired_grad_operators = [
            CreateOperator(
                'DirectGradient',
                'out_grad', ['hidden1_grad', 'hidden2_grad']
            ),
            CreateOperator(
                'DirectGradient',
                'hidden2_grad', '_in_grad_autosplit_0'
            ),
            CreateOperator(
                'DirectGradient',
                'hidden1_grad', '_in_grad_autosplit_1'
            ),
            CreateOperator(
                'Sum',
                ['_in_grad_autosplit_0', '_in_grad_autosplit_1'], 'in_grad'
            ),
            CreateOperator(
                'DirectGradient',
                'in_grad', 'in_grad'
            ),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testMultiUseInputAndMultipleVersionsBig(self):
        """Test gradient for the following case:

        in -> in
        in -> hidden1, hidden2
        hidden1, hidden2 -> in
        in -> hidden3, hidden4, hidden5
        hidden3, hidden4, hidden5 -> out
        """
        operators = [
            CreateOperator('Direct', 'in', 'in'),
            CreateOperator('Direct', 'in', 'hidden1'),
            CreateOperator('Direct', 'in', 'hidden2'),
            CreateOperator('Direct', ['hidden1', 'hidden2'], 'in'),
            CreateOperator('Direct', 'in', 'hidden3'),
            CreateOperator('Direct', 'in', 'hidden4'),
            CreateOperator('Direct', 'in', 'hidden5'),
            CreateOperator('Direct', ['hidden3', 'hidden4', 'hidden5'], 'out'),
        ]
        desired_grad_operators = [
            CreateOperator(
                'DirectGradient',
                'out_grad', ['hidden3_grad', 'hidden4_grad', 'hidden5_grad']
            ),
            CreateOperator(
                'DirectGradient',
                'hidden5_grad', '_in_grad_autosplit_0'
            ),
            CreateOperator(
                'DirectGradient',
                'hidden4_grad', '_in_grad_autosplit_1'
            ),
            CreateOperator(
                'DirectGradient',
                'hidden3_grad', '_in_grad_autosplit_2'
            ),
            CreateOperator(
                'Sum',
                ['_in_grad_autosplit_0', '_in_grad_autosplit_1',
                 '_in_grad_autosplit_2'],
                'in_grad'
            ),
            CreateOperator(
                'DirectGradient',
                'in_grad', ['hidden1_grad', 'hidden2_grad']
            ),
            CreateOperator(
                'DirectGradient',
                'hidden2_grad', '_in_grad_autosplit_0'
            ),
            CreateOperator(
                'DirectGradient',
                'hidden1_grad', '_in_grad_autosplit_1'
            ),
            CreateOperator(
                'Sum',
                ['_in_grad_autosplit_0', '_in_grad_autosplit_1'],
                'in_grad'
            ),
            CreateOperator(
                'DirectGradient',
                'in_grad', 'in_grad'
            ),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        for s in gradients:
            print(str(s))
        self.assertEqual(gradients, desired_grad_operators)

    def testGradientMappingUsingSumOp(self):
        """Since Sum is used in accumulating gradients, we will test if
        it is OK to also explicitly use it in the graph."""
        operators = [
            CreateOperator('FC', ['in', 'w', 'b'], 'fc'),
            CreateOperator('Sum', 'fc', 'agg'),
            CreateOperator('AveragedLoss', 'agg', 'loss'),
        ]
        # This should run correctly.
        gradient_ops, _ = GradientRegistry.GetBackwardPass(
            operators, {'loss': 'loss_grad'})
        for s in gradient_ops:
            print(str(s))

    def testGradientCalculationWithPrint(self):
        """Test a common use case where we have Print in the forward pass."""
        operators = [
            CreateOperator('FC', ['in', 'w', 'b'], 'fc'),
            CreateOperator('Print', 'fc', []),
            CreateOperator('AveragedLoss', 'fc', 'loss'),
        ]
        desired_grad_operators = [
            CreateOperator('AveragedLossGradient',
                           ['fc', 'loss_grad'], 'fc_grad'),
            CreateOperator('FCGradient', ['in', 'w', 'fc_grad'],
                           ['w_grad', 'b_grad', 'in_grad']),
        ]
        for g in desired_grad_operators:
            g.is_gradient_op = 1
        # This should run correctly.
        gradient_ops, _ = GradientRegistry.GetBackwardPass(
            operators, {'loss': 'loss_grad'})
        for s in gradient_ops:
            print(str(s))
        self.assertEqual(gradient_ops, desired_grad_operators)

    def testStopGradient(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('StopGradient', 'hidden', 'hidden2'),
            CreateOperator('Direct', 'hidden2', 'out'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'out_grad', 'hidden2_grad'),
        ]
        gradients, _ = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)

    def testStopGradientInplace(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('StopGradient', 'hidden', 'hidden'),
            CreateOperator('Direct', 'hidden', 'out'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'out_grad', 'hidden_grad'),
        ]
        gradients, grad_map = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)
        self.assertEqual(grad_map, {'out': 'out_grad'})

    def testStopGradientWithMultiUseOperators(self):
        operators = [
            CreateOperator('Direct', 'in', 'hidden'),
            CreateOperator('Direct', 'hidden', 'hidden2'),
            CreateOperator('StopGradient', 'hidden', 'hidden3'),
            CreateOperator('Direct', ['hidden2', 'hidden3'], 'out'),
        ]
        desired_grad_operators = [
            CreateOperator('DirectGradient', 'out_grad',
                           ['hidden2_grad', 'hidden3_grad']),
            CreateOperator('DirectGradient', 'hidden2_grad', 'hidden_grad'),
            CreateOperator('DirectGradient', 'hidden_grad', 'in_grad'),
        ]
        gradients, grad_map = GradientRegistry.GetBackwardPass(
            operators, {'out': 'out_grad'})
        self.assertEqual(gradients, desired_grad_operators)
        self.assertEqual(
            grad_map, {'out': 'out_grad', 'hidden2': 'hidden2_grad',
                       'hidden3': 'hidden3_grad', 'hidden': 'hidden_grad',
                       'in': 'in_grad'})

# Skip if sparse operators are not available
@unittest.skipIf(not core.IsOperator('SparseFunHash'),
                 'Sparse operators not available')
class TestSparseGradientsAccumulation(test_util.TestCase):
    def testSparseAccumulationWithValues(self):
        # The gradient for "Gather" only computes values. indices are directly
        # passed from the input
        #
        # x1-->Gather-->x4-->
        #        |          |
        # x2-----+     DotProduct-->x6
        #        |          |
        # x3-->Gather-->x5-->
        net = core.Net("test_net")
        net.Gather(["x2", "x1"], "x4")
        net.Gather(["x2", "x3"], "x5")
        net.DotProduct(["x4", "x5"], "x6")
        net.AddGradientOperators(["x6"])
        sum_op_i = net.Proto().op[-2]
        sum_op_v = net.Proto().op[-1]
        self.assertEqual(sum_op_i.input[0], "x3")
        self.assertEqual(sum_op_i.input[1], "x1")
        self.assertEqual(sum_op_i.output[0], "x2_grad_indices_concat")
        self.assertEqual(sum_op_v.input[0], "x5_grad")
        self.assertEqual(sum_op_v.input[1], "x4_grad")
        self.assertEqual(sum_op_v.output[0], "x2_grad_values_concat")

    def testSparseGradientToDense(self):
        #
        #                                        x1-->Gather-->x4-->
        #                                                 |        |
        # x0, w, b-->FC-->x2-->EnsureDenseGradient-->x2---+  DotProduct-->x6
        #                                                 |        |
        #                                        x3-->Gather-->x5-->
        net = core.Net("test_net")
        net.FC(["x0", "w", "b"], "x2")
        net.EnsureDense(["x2"], "x2")
        net.Gather(["x2", "x1"], "x4")
        net.Gather(["x2", "x3"], "x5")
        net.DotProduct(["x4", "x5"], "x6")
        net.AddGradientOperators(["x6"])
        ensure_dense_op = net.Proto().op[-2]
        self.assertEqual(ensure_dense_op.input[0], "x2_grad_indices_concat")
        self.assertEqual(ensure_dense_op.input[1], "x2_grad_values_concat")
        self.assertEqual(ensure_dense_op.output[0], "x2_grad")

    def testSparseAccumulationWithIndicesAndValues(self):
        # The gradient for "SparseFunHash" computes both indices and values
        #
        # x1-------->
        #           |
        # x2---->   |
        #       |   |
        # x3---SparseFunHash-->x8
        #       /               \
        # x4---+            DotProduct-->x10
        #       \               /
        # x5---SparseFunHash-->x9
        #       |   |
        # x6---->   |
        #           |
        # x7-------->
        net = core.Net("test_net")
        net.SparseFunHash(["x1", "x2", "x3", "x4"], "x8")
        net.SparseFunHash(["x5", "x6", "x7", "x4"], "x9")
        net.DotProduct(["x8", "x9"], "x10")
        net.AddGradientOperators(["x10"])
        sum_op_i = net.Proto().op[-2]
        sum_op_v = net.Proto().op[-1]
        self.assertEqual(sum_op_i.input[0], "_x4_grad_indices_autosplit_0")
        self.assertEqual(sum_op_i.input[1], "_x4_grad_indices_autosplit_1")
        self.assertEqual(sum_op_i.output[0], "x4_grad_indices_concat")
        self.assertEqual(sum_op_v.input[0], "_x4_grad_values_autosplit_0")
        self.assertEqual(sum_op_v.input[1], "_x4_grad_values_autosplit_1")
        self.assertEqual(sum_op_v.output[0], "x4_grad_values_concat")


class TestGradientsAccumulationWithNoGradientOps(test_util.TestCase):
    def testNormalAccumulation(self):
        #  x1-->Relu--x2----------------->DotProduct-->x4
        #                |                 |
        #                 -->Softmax-->x3-->
        net = core.Net("test_net")
        net.Relu("x1", "x2")
        net.Softmax("x2", "x3")
        net.DotProduct(["x2", "x3"], "x4")
        net.AddGradientOperators(["x4"])
        sum_op = net.Proto().op[-2]
        self.assertEqual(sum_op.input[0], "_x2_grad_autosplit_0")
        self.assertEqual(sum_op.input[1], "_x2_grad_autosplit_1")
        self.assertEqual(sum_op.output[0], "x2_grad")

    def testAccumulationWithNoGradientBranch(self):
        #                 -->PRINT
        #                |
        #  x1-->Relu--x2----------------->DotProduct-->x4
        #                |                 |
        #                 -->Softmax-->x3-->
        net = core.Net("test_net")
        net.Relu("x1", "x2")
        net.Print("x2", [])
        net.Softmax("x2", "x3")
        net.DotProduct(["x2", "x3"], "x4")
        net.AddGradientOperators(["x4"])
        sum_op = net.Proto().op[-2]
        self.assertEqual(sum_op.input[0], "_x2_grad_autosplit_0")
        self.assertEqual(sum_op.input[1], "_x2_grad_autosplit_1")
        self.assertEqual(sum_op.output[0], "x2_grad")


class TestGradientsAccumulationWithPassThroughGradients(test_util.TestCase):
    def testAddOpInMiddle(self):
        #  x1-->Relu--x2----------------->Add-->x4
        #                |                 |
        #                 -->Softmax-->x3-->
        #
        # Expected gradient graph:
        #
        #  x1_g<--ReluG<--x2_g<--Sum<------------<---------x4_g
        #                          |                       |
        #                           <--_x2_g_split_0<--SoftmaxG
        net = core.Net("test_net")
        net.Relu("x1", "x2")
        net.Softmax("x2", "x3")
        net.Add(["x2", "x3"], "x4")
        input_to_grad = net.AddGradientOperators({"x4": "x4_grad"})
        sum_op = net.Proto().op[-2]
        self.assertEqual(sum_op.input[0], "x4_grad")
        self.assertEqual(sum_op.input[1], "_x2_grad_autosplit_0")
        self.assertEqual(sum_op.output[0], "x2_grad")
        self.assertEqual(input_to_grad["x1"], "x1_grad")

    def testSubOpInMiddle(self):
        #  x1-->Relu--x2----------------->Sub-->x4
        #                |                 |
        #                 -->Softmax-->x3-->
        #
        # Expected gradient graph:
        #
        #  x1_g<--ReluG<--x2_g<--Sum<------------<-----------------------x4_g
        #                          |                                      |
        #                           <--_x2_g_split_0<--SoftmaxG<--x3_g<--neg
        net = core.Net("test_net")
        net.Relu("x1", "x2")
        net.Softmax("x2", "x3")
        net.Sub(["x2", "x3"], "x4")
        input_to_grad = net.AddGradientOperators({"x4": "x4_grad"})
        print(str(net.Proto()))
        sum_op = net.Proto().op[-2]
        self.assertEqual(sum_op.input[0], "x4_grad")
        self.assertEqual(sum_op.input[1], "_x2_grad_autosplit_0")
        self.assertEqual(sum_op.output[0], "x2_grad")
        self.assertEqual(input_to_grad["x1"], "x1_grad")

    def testAddOpAtLeaf(self):
        # x1
        #   \
        #    -->Add-->x4
        #   /           \
        # x2             -->DotProduct-->x6
        #   \           /
        #    -->Add-->x5
        #   /
        # x3
        #
        # Expected gradient graph:
        #
        #  x2_g<--Sum<--x4_g<--DotProductG<--x6_g
        #          |                |                       |
        #           <---x5_g<-------
        net = core.Net("test_net")
        net.Add(["x1", "x2"], "x4")
        net.Add(["x2", "x3"], "x5")
        net.DotProduct(["x4", "x5"], "x6")
        input_to_grad = net.AddGradientOperators({"x6": "x6_grad"})
        sum_op = net.Proto().op[-1]
        self.assertEqual(sum_op.input[0], "x5_grad")
        self.assertEqual(sum_op.input[1], "x4_grad")
        self.assertEqual(sum_op.output[0], "x2_grad")
        self.assertEqual(input_to_grad["x1"], "x4_grad")
        self.assertEqual(input_to_grad["x2"], "x2_grad")
        self.assertEqual(input_to_grad["x3"], "x5_grad")

    def testSubOpAtLeaf(self):
        # x1
        #   \
        #    -->Sub-->x4
        #   /           \
        # x2             -->DotProduct-->x6
        #   \           /
        #    -->Sub-->x5
        #   /
        # x3
        #
        # Expected gradient graph:
        #
        #  x2_g<-------Sum<--x2_g_split_0<--neg<--x4_g<--DotProductG<--x6_g
        #               |                                       |
        #  x3_g<--neg<--<--x5_g<--------------------------------
        net = core.Net("test_net")
        net.Sub(["x1", "x2"], "x4")
        net.Sub(["x2", "x3"], "x5")
        net.DotProduct(["x4", "x5"], "x6")
        input_to_grad = net.AddGradientOperators({"x6": "x6_grad"})
        sum_op = net.Proto().op[-1]
        self.assertEqual(sum_op.input[0], "x5_grad")
        self.assertEqual(sum_op.input[1], "_x2_grad_autosplit_0")
        self.assertEqual(sum_op.output[0], "x2_grad")
        self.assertEqual(input_to_grad["x1"], "x4_grad")
        self.assertEqual(input_to_grad["x2"], "x2_grad")
        self.assertEqual(input_to_grad["x3"], "x3_grad")

    def testMultiLayerAddOps(self):
        # x1
        #   \
        #    -->Add-->x4
        #   /           \
        # x2             -->Add-->x6
        #   \           /
        #    -->Add-->x5
        #   /
        # x3
        #
        # Expected gradient graph:
        #
        #  x2_g<--Sum<-----x6_g
        #          |         |
        #           <--------
        net = core.Net("test_net")
        net.Add(["x1", "x2"], "x4")
        net.Add(["x2", "x3"], "x5")
        net.Add(["x4", "x5"], "x6")
        input_to_grad = net.AddGradientOperators({"x6": "x6_grad"})
        sum_op = net.Proto().op[-1]
        self.assertEqual(sum_op.input[0], "x6_grad")
        self.assertEqual(sum_op.input[1], "x6_grad")
        self.assertEqual(sum_op.output[0], "x2_grad")
        self.assertEqual(input_to_grad["x1"], "x6_grad")
        self.assertEqual(input_to_grad["x2"], "x2_grad")
        self.assertEqual(input_to_grad["x3"], "x6_grad")

    def testMultiLayerSubOps(self):
        # x1
        #   \
        #    -->Sub-->x4
        #   /           \
        # x2             -->Sub-->x6
        #   \           /
        #    -->Sub-->x5
        #   /
        # x3
        #
        # Expected gradient graph:
        #
        #  x2_g<--Sum<-----x6_g
        #          |         |
        #           <--------
        net = core.Net("test_net")
        net.Sub(["x1", "x2"], "x4")
        net.Sub(["x2", "x3"], "x5")
        net.Sub(["x4", "x5"], "x6")
        input_to_grad = net.AddGradientOperators({"x6": "x6_grad"})
        sum_op = net.Proto().op[-1]
        self.assertEqual(sum_op.input[0], "x5_grad")
        self.assertEqual(sum_op.input[1], "_x2_grad_autosplit_0")
        self.assertEqual(sum_op.output[0], "x2_grad")
        self.assertEqual(input_to_grad["x1"], "x6_grad")
        self.assertEqual(input_to_grad["x2"], "x2_grad")
        self.assertEqual(input_to_grad["x3"], "x3_grad")


if __name__ == '__main__':
    unittest.main()
