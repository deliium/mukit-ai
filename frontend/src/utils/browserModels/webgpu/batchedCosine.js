/**
 * WebGPU batched cosine similarity (WGSL compute) for L2-normalized vectors.
 * Falls back to CPU cosine on any init/dispatch failure.
 */

import { createAppLogger } from '../../appLogger.js';
import { FALLBACK_REASONS } from '../constants.js';
import { batchedCosineCpu } from '../symbolicFeaturesV1.js';

const log = createAppLogger('browserModels');

const WGSL = `
struct Params {
  dims: u32,
  rows: u32,
  _pad0: u32,
  _pad1: u32,
};

@group(0) @binding(0) var<storage, read> query: array<f32>;
@group(0) @binding(1) var<storage, read> matrix: array<f32>;
@group(0) @binding(2) var<storage, read_write> scores: array<f32>;
@group(0) @binding(3) var<uniform> params: Params;

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let row = gid.x;
  if (row >= params.rows) {
    return;
  }
  var dot: f32 = 0.0;
  let base = row * params.dims;
  for (var i: u32 = 0u; i < params.dims; i = i + 1u) {
    dot = dot + query[i] * matrix[base + i];
  }
  scores[row] = dot;
}
`;

/**
 * @param {number[]} query
 * @param {number[][]} matrix
 * @param {{ gpu?: GPU, device?: GPUDevice | null }} [opts]
 * @returns {Promise<{ scores: number[], device: 'webgpu' | 'browser_cpu', fallback_reason?: string }>}
 */
export async function batchedCosineSimilarity(query, matrix, opts = {}) {
  const rows = matrix.length;
  const dims = query.length;
  if (!rows) {
    return { scores: [], device: 'browser_cpu' };
  }
  for (const row of matrix) {
    if (!row || row.length !== dims) {
      return {
        scores: batchedCosineCpu(query, matrix),
        device: 'browser_cpu',
        fallback_reason: FALLBACK_REASONS.HOST_ERROR,
      };
    }
  }

  const gpu = opts.gpu
    || (typeof navigator !== 'undefined' ? navigator.gpu : undefined);
  if (!gpu || typeof gpu.requestAdapter !== 'function') {
    log.warn('WebGPU unavailable for batched cosine', {
      reason: FALLBACK_REASONS.WEBGPU_UNAVAILABLE,
    });
    return {
      scores: batchedCosineCpu(query, matrix),
      device: 'browser_cpu',
      fallback_reason: FALLBACK_REASONS.WEBGPU_UNAVAILABLE,
    };
  }

  try {
    let device = opts.device || null;
    if (!device) {
      const adapter = await gpu.requestAdapter();
      if (!adapter) {
        throw new Error('no adapter');
      }
      device = await adapter.requestDevice();
    }

    const queryData = new Float32Array(query);
    const matrixData = new Float32Array(rows * dims);
    for (let r = 0; r < rows; r += 1) {
      matrixData.set(matrix[r], r * dims);
    }
    const scoresData = new Float32Array(rows);
    const paramsData = new Uint32Array([dims, rows, 0, 0]);

    const queryBuf = device.createBuffer({
      size: queryData.byteLength,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
    });
    const matrixBuf = device.createBuffer({
      size: matrixData.byteLength,
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
    });
    const scoresBuf = device.createBuffer({
      size: Math.max(4, scoresData.byteLength),
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST,
    });
    const paramsBuf = device.createBuffer({
      size: paramsData.byteLength,
      usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
    });
    const readBuf = device.createBuffer({
      size: Math.max(4, scoresData.byteLength),
      usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
    });

    device.queue.writeBuffer(queryBuf, 0, queryData);
    device.queue.writeBuffer(matrixBuf, 0, matrixData);
    device.queue.writeBuffer(paramsBuf, 0, paramsData);

    const module = device.createShaderModule({ code: WGSL });
    const pipeline = device.createComputePipeline({
      layout: 'auto',
      compute: { module, entryPoint: 'main' },
    });
    const bindGroup = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: { buffer: queryBuf } },
        { binding: 1, resource: { buffer: matrixBuf } },
        { binding: 2, resource: { buffer: scoresBuf } },
        { binding: 3, resource: { buffer: paramsBuf } },
      ],
    });

    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass();
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(Math.ceil(rows / 64));
    pass.end();
    encoder.copyBufferToBuffer(scoresBuf, 0, readBuf, 0, scoresData.byteLength);
    device.queue.submit([encoder.finish()]);

    await readBuf.mapAsync(GPUMapMode.READ);
    const copy = new Float32Array(readBuf.getMappedRange().slice(0));
    readBuf.unmap();

    const scores = Array.from(copy);
    log.info('WebGPU batched cosine complete', {
      rows,
      dims,
      device: 'webgpu',
    });
    return { scores, device: 'webgpu' };
  } catch (err) {
    log.warn('WebGPU batched cosine failed; CPU fallback', {
      reason: FALLBACK_REASONS.WEBGPU_INIT_FAILED,
      error_type: err?.name || 'Error',
    });
    return {
      scores: batchedCosineCpu(query, matrix),
      device: 'browser_cpu',
      fallback_reason: FALLBACK_REASONS.WEBGPU_INIT_FAILED,
    };
  }
}
