#include "caffe2/core/context_gpu.h"
#include "caffe2/operators/utility_ops.h"

namespace caffe2 {

template <>
class CopyOnDeviceLikeOp<CUDAContext, CUDAContext, CUDAContext>
    : public Operator<CUDAContext> {
 public:
  CopyOnDeviceLikeOp(const OperatorDef& operator_def, Workspace* ws)
      : Operator<CUDAContext>(operator_def, ws) {}
  USE_OPERATOR_FUNCTIONS(CUDAContext);

  bool RunOnDevice() override {
    auto& input = Input(0);
    auto* output = OperatorBase::Output<Tensor<CUDAContext>>(0);
    CUDAContext context(GetGPUIDForPointer(Input(1).raw_data()));
    output->ResizeLike(input);
    context.template CopyItems<CUDAContext, CUDAContext>(
        input.meta(),
        input.size(),
        input.raw_data(),
        output->raw_mutable_data(input.meta()));
    return true;
  }
};

namespace {

REGISTER_CUDA_OPERATOR(Print, PrintOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(Flatten, FlattenOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(FlattenToVec, FlattenToVecOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(Squeeze, SqueezeOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(ExpandDims, ExpandDimsOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(Alias, AliasOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(ResizeLike, ResizeLikeOp<CUDAContext>);
REGISTER_CUDA_OPERATOR(Reshape, ReshapeOp<float, CUDAContext>);
REGISTER_CUDA_OPERATOR(Sum, SumOp<float, CUDAContext>);
REGISTER_CUDA_OPERATOR(SumElements, SumElementsOp<float, CUDAContext>);
REGISTER_CUDA_OPERATOR(
    SumElementsGradient,
    SumElementsGradientOp<float, CUDAContext>);
REGISTER_CUDA_OPERATOR(WeightedSum, WeightedSumOp<float, CUDAContext>);
REGISTER_CUDA_OPERATOR(Shape, ShapeOp<CUDAContext>);
// From whatever the current context, ensure the output is TensorCPU
REGISTER_CUDA_OPERATOR(
    EnsureCPUOutput,
    CopyOp<CUDAContext, CPUContext, CUDAContext>);
// From CPU, copy it to whatever the current context
REGISTER_CUDA_OPERATOR(
    CopyFromCPUInput,
    CopyOp<CUDAContext, CUDAContext, CPUContext>);

// CopyGPUToCPU and CopyCPUToGPU should both be carried out in a cuda context,
// since gpu code will be involved.
REGISTER_CUDA_OPERATOR(
    CopyGPUToCPU,
    CopyOp<CUDAContext, CPUContext, CUDAContext>);
REGISTER_CUDA_OPERATOR(
    CopyCPUToGPU,
    CopyOp<CUDAContext, CUDAContext, CPUContext>);
// If we only specify Copy, we assume that it is a gpu to gpu copy - maybe
// involving different GPUs.
REGISTER_CUDA_OPERATOR(Copy, CopyOp<CUDAContext, CUDAContext, CUDAContext>);

REGISTER_CUDA_OPERATOR(
    CopyOnDeviceLike,
    CopyOnDeviceLikeOp<CUDAContext, CUDAContext, CUDAContext>);

REGISTER_CUDA_OPERATOR(UnsafeCoalesce, UnsafeCoalesceOp<CUDAContext>);

} // namespace
} // namespace caffe2
