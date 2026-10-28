"""Compiled version of the authored-mesh pixel-center rasterizer.

Uses the same barycentric tests and edge clustering as geometry.py. No fastmath:
boundary decisions must retain the reference floating-point operation order.
"""
import math
import numpy as np
from numba import njit


@njit(cache=True)
def raster_hits(px, py, pz, facing, width, height, epsilon, ray_limit):
    ids, distances, faces = [], [], []
    for fi in range(len(px)):
        if abs(facing[fi]) <= epsilon:
            continue
        x, y, z = px[fi], py[fi], pz[fi]
        lx = max(0, math.ceil(min(x))); hx = min(width - 1, math.floor(max(x)))
        ly = max(0, math.ceil(min(y))); hy = min(height - 1, math.floor(max(y)))
        if lx > hx or ly > hy:
            continue
        det = (y[1]-y[2])*(x[0]-x[2]) + (x[2]-x[1])*(y[0]-y[2])
        if abs(det) <= epsilon:
            continue
        for gy in range(ly, hy + 1):
            for gx in range(lx, hx + 1):
                a = ((y[1]-y[2])*(gx-x[2]) + (x[2]-x[1])*(gy-y[2])) / det
                b = ((y[2]-y[0])*(gx-x[2]) + (x[0]-x[2])*(gy-y[2])) / det
                c = 1-a-b
                t = a*z[0] + b*z[1] + c*z[2]
                if a >= -epsilon and b >= -epsilon and c >= -epsilon and 0 <= t <= ray_limit:
                    ids.append(gy*width+gx); distances.append(t); faces.append(fi)
    return np.asarray(ids, dtype=np.int32), np.asarray(distances), np.asarray(faces, dtype=np.int32)


@njit(cache=True)
def inward_intervals(ids, distance, faces, facing, cluster):
    rays = np.empty(len(ids), dtype=np.int32)
    starts = np.empty(len(ids), dtype=np.float64)
    ends = np.empty(len(ids), dtype=np.float64)
    cosines = np.empty(len(ids), dtype=np.float64)
    count = 0
    begin = 0
    while begin < len(ids):
        end = begin+1
        while end < len(ids) and ids[end] == ids[begin]:
            end += 1
        unique = [begin]
        for pos in range(begin+1, end):
            if distance[pos]-distance[unique[-1]] > cluster:
                unique.append(pos)
        for j in range(len(unique)):
            pos = unique[j]
            if facing[faces[pos]] >= 0:
                continue
            exit_ = np.inf
            if j+1 < len(unique) and facing[faces[unique[j+1]]] > 0:
                exit_ = distance[unique[j+1]]
            rays[count] = ids[pos]; starts[count] = distance[pos]; ends[count] = exit_; cosines[count] = -facing[faces[pos]]
            count += 1
        begin = end
    return rays[:count], starts[:count], ends[:count], cosines[:count]
