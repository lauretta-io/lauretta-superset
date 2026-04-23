/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import React, { useMemo, useState } from 'react';
import { css, styled, useTheme } from '@superset-ui/core';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import { NodeStats } from './types';

interface FlowGraphProps {
  selectedNode: NodeStats;
  nodeStatsMap: Map<string, NodeStats>;
  formatDwellTime: (mins: number) => string;
  isUniqueCustomerView?: boolean;
}

interface GraphNode {
  id: string;
  label: string;
  x: number;
  y: number;
  type: 'incoming' | 'center' | 'outgoing';
  count?: number;
  percentage?: number;
}

interface GraphEdge {
  source: string;
  target: string;
  count: number;
  percentage: number;
  type: 'incoming' | 'outgoing';
}

const GraphContainer = styled.div`
  ${({ theme }) => css`
    width: 100%;
    height: 640px;
    position: relative;
    background: linear-gradient(
      160deg,
      ${theme.colors.grayscale.light5} 0%,
      #f4f8ff 45%,
      #eefbf7 100%
    );
    border-radius: ${theme.gridUnit * 3}px;
    border: 1px solid ${theme.colors.grayscale.light2};
    overflow: hidden;
    box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);

    .react-transform-wrapper {
      width: 100%;
      height: 100%;
    }

    .react-transform-component {
      width: 100%;
      height: 100%;
    }

    .zoom-controls {
      position: absolute;
      top: 16px;
      right: 16px;
      z-index: 20;
      display: flex;
      gap: 6px;
      background: rgba(255, 255, 255, 0.85);
      backdrop-filter: blur(8px);
      padding: 8px;
      border-radius: 12px;
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.12);
    }

    .zoom-btn {
      padding: 0;
      border: 1px solid ${theme.colors.grayscale.light2};
      background: white;
      border-radius: 8px;
      cursor: pointer;
      width: 34px;
      height: 34px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s ease;
      color: ${theme.colors.grayscale.dark1};
      font-size: 17px;
      font-weight: bold;

      &:hover {
        color: ${theme.colors.primary.base};
        border-color: ${theme.colors.primary.base};
        background: ${theme.colors.primary.light5};
      }

      &:active {
        transform: scale(0.97);
      }
    }

    .graph-legend {
      position: absolute;
      bottom: 16px;
      left: 16px;
      z-index: 20;
      display: flex;
      gap: 16px;
      background: rgba(255, 255, 255, 0.88);
      backdrop-filter: blur(8px);
      padding: 8px 14px;
      border-radius: 10px;
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);
      font-size: ${theme.typography.sizes.xs}px;
    }

    .legend-item {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }

    .legend-dot.incoming {
      background: linear-gradient(135deg, #34d399 0%, #059669 100%);
    }

    .legend-dot.center {
      background: linear-gradient(
        135deg,
        ${theme.colors.primary.light1} 0%,
        ${theme.colors.primary.dark1} 100%
      );
    }

    .legend-dot.outgoing {
      background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);
    }

    .drag-hint {
      position: absolute;
      bottom: 16px;
      right: 16px;
      z-index: 20;
      background: rgba(15, 23, 42, 0.7);
      color: #f8fafc;
      padding: 7px 12px;
      border-radius: 8px;
      font-size: ${theme.typography.sizes.xs}px;
      letter-spacing: 0.2px;
      opacity: 0.9;
    }
  `}
`;

const NODE_RADIUS = 54;
const CENTER_NODE_RADIUS = 68;
const GRAPH_WIDTH = 1400;
const GRAPH_HEIGHT = 760;
const CENTER_X = GRAPH_WIDTH / 2;
const CENTER_Y = GRAPH_HEIGHT / 2;
const INCOMING_X = 220;
const OUTGOING_X = GRAPH_WIDTH - 220;

function splitLabelToLines(label: string, maxCharsPerLine: number): string[] {
  const normalized = label.trim();
  if (!normalized) return [''];

  const words = normalized.split(/\s+/);
  const lines: string[] = [];
  let current = '';

  words.forEach(word => {
    if (word.length > maxCharsPerLine) {
      if (current) {
        lines.push(current);
        current = '';
      }
      for (let i = 0; i < word.length; i += maxCharsPerLine) {
        lines.push(word.slice(i, i + maxCharsPerLine));
      }
      return;
    }

    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= maxCharsPerLine) {
      current = candidate;
    } else {
      lines.push(current);
      current = word;
    }
  });

  if (current) {
    lines.push(current);
  }

  return lines.slice(0, 4);
}

export default function FlowGraph({
  selectedNode,
  nodeStatsMap,
  formatDwellTime,
  isUniqueCustomerView = false,
}: FlowGraphProps) {
  const theme = useTheme();
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  // Build a three-column layout (incoming -> center -> outgoing) and matching edges.
  const { nodes, edges } = useMemo(() => {
    const graphNodes: GraphNode[] = [];
    const graphEdges: GraphEdge[] = [];

    // Center node
    graphNodes.push({
      id: 'center',
      label: selectedNode.nodeName,
      x: CENTER_X,
      y: CENTER_Y,
      type: 'center',
      count: selectedNode.totalVisits,
    });

    // Incoming nodes (left side) - Select Top 5
    const incomingFlows = selectedNode.incomingFlows.slice(0, 5);
    const incomingCount = incomingFlows.length;
    // Spread side nodes vertically to avoid overlap when flow count changes.
    const incomingSpacing =
      incomingCount > 1 ? (GRAPH_HEIGHT - 100) / (incomingCount - 1) : 0;
    const incomingStartY = incomingCount > 1 ? 50 : CENTER_Y;

    incomingFlows.forEach((flow, idx) => {
      const y =
        incomingCount > 1
          ? incomingStartY + idx * incomingSpacing
          : incomingStartY;
      graphNodes.push({
        id: `incoming-${idx}`,
        label: flow.source,
        x: INCOMING_X,
        y,
        type: 'incoming',
        count: flow.count,
        percentage: flow.percentage,
      });
      graphEdges.push({
        source: `incoming-${idx}`,
        target: 'center',
        count: flow.count,
        percentage: flow.percentage,
        type: 'incoming',
      });
    });

    // Outgoing nodes (right side) - Select Top 5
    const outgoingFlows = selectedNode.outgoingFlows.slice(0, 5);
    const outgoingCount = outgoingFlows.length;
    // Mirror the same spacing logic for outgoing nodes on the right side.
    const outgoingSpacing =
      outgoingCount > 1 ? (GRAPH_HEIGHT - 100) / (outgoingCount - 1) : 0;
    const outgoingStartY = outgoingCount > 1 ? 50 : CENTER_Y;

    outgoingFlows.forEach((flow, idx) => {
      const y =
        outgoingCount > 1
          ? outgoingStartY + idx * outgoingSpacing
          : outgoingStartY;
      graphNodes.push({
        id: `outgoing-${idx}`,
        label: flow.target,
        x: OUTGOING_X,
        y,
        type: 'outgoing',
        count: flow.count,
        percentage: flow.percentage,
      });
      graphEdges.push({
        source: 'center',
        target: `outgoing-${idx}`,
        count: flow.count,
        percentage: flow.percentage,
        type: 'outgoing',
      });
    });

    return { nodes: graphNodes, edges: graphEdges };
  }, [selectedNode]);

  // Get node by id
  const getNodeById = (id: string) => nodes.find(n => n.id === id);

  // Draw smooth curved routes between circles instead of straight lines.
  const generatePath = (edge: GraphEdge) => {
    const source = getNodeById(edge.source);
    const target = getNodeById(edge.target);
    if (!source || !target) return '';

    const sourceRadius =
      source.type === 'center' ? CENTER_NODE_RADIUS : NODE_RADIUS;
    const targetRadius =
      target.type === 'center' ? CENTER_NODE_RADIUS : NODE_RADIUS;

    // Trim line endpoints to each circle boundary so paths do not run through nodes.
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const dist = Math.sqrt(dx * dx + dy * dy);

    const startX = source.x + (dx / dist) * sourceRadius;
    const startY = source.y + (dy / dist) * sourceRadius;
    const endX = target.x - (dx / dist) * targetRadius;
    const endY = target.y - (dy / dist) * targetRadius;

    // Control points make the curve bow horizontally for a cleaner graph look.
    const cpOffset = Math.abs(endX - startX) * 0.4;
    const cp1x = startX + cpOffset;
    const cp1y = startY;
    const cp2x = endX - cpOffset;
    const cp2y = endY;

    return `M ${startX} ${startY} C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${endX} ${endY}`;
  };

  // Get fill color based on node type
  const getNodeGradientId = (type: string) => {
    switch (type) {
      case 'incoming':
        return 'url(#incomingGradient)';
      case 'outgoing':
        return 'url(#outgoingGradient)';
      default:
        return 'url(#centerGradient)';
    }
  };

  // Get stroke color based on edge type
  const getEdgeColor = (type: string) => {
    return type === 'incoming' ? '#34d399' : '#fbbf24';
  };

  const isEdgeConnectedToHoveredNode = (edge: GraphEdge) => {
    if (!hoveredNodeId) {
      return true;
    }
    return edge.source === hoveredNodeId || edge.target === hoveredNodeId;
  };

  const isNodeConnectedToHoveredNode = (nodeId: string) => {
    if (!hoveredNodeId) {
      return true;
    }
    if (nodeId === hoveredNodeId) {
      return true;
    }
    return edges.some(
      edge =>
        (edge.source === hoveredNodeId && edge.target === nodeId) ||
        (edge.target === hoveredNodeId && edge.source === nodeId),
    );
  };

  return (
    <GraphContainer>
      <TransformWrapper
        initialScale={0.85}
        initialPositionX={0}
        initialPositionY={0}
        minScale={0.5}
        maxScale={2.5}
        limitToBounds={false}
        wheel={{ step: 0.1 }}
        pinch={{ step: 5 }}
        panning={{ velocityDisabled: false }}
        doubleClick={{ disabled: false, step: 0.5 }}
      >
        {({ zoomIn, zoomOut, resetTransform }) => (
          <>
            <div className="zoom-controls">
              <button
                className="zoom-btn"
                onClick={() => zoomIn()}
                title="Zoom In"
              >
                +
              </button>
              <button
                className="zoom-btn"
                onClick={() => zoomOut()}
                title="Zoom Out"
              >
                −
              </button>
              <button
                className="zoom-btn"
                onClick={() => resetTransform()}
                title="Reset"
              >
                ⟲
              </button>
            </div>

            <TransformComponent>
              <svg
                width={GRAPH_WIDTH}
                height={GRAPH_HEIGHT}
                viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`}
                style={{ display: 'block' }}
              >
                {/* Gradient definitions */}
                <defs>
                  <linearGradient
                    id="incomingGradient"
                    x1="0%"
                    y1="0%"
                    x2="100%"
                    y2="100%"
                  >
                    <stop offset="0%" stopColor="#34d399" />
                    <stop offset="100%" stopColor="#059669" />
                  </linearGradient>
                  <linearGradient
                    id="outgoingGradient"
                    x1="0%"
                    y1="0%"
                    x2="100%"
                    y2="100%"
                  >
                    <stop offset="0%" stopColor="#fbbf24" />
                    <stop offset="100%" stopColor="#f59e0b" />
                  </linearGradient>
                  <linearGradient
                    id="centerGradient"
                    x1="0%"
                    y1="0%"
                    x2="100%"
                    y2="100%"
                  >
                    <stop offset="0%" stopColor={theme.colors.primary.light1} />
                    <stop
                      offset="100%"
                      stopColor={theme.colors.primary.dark1}
                    />
                  </linearGradient>
                  {/* Arrow markers */}
                  <marker
                    id="arrowIncoming"
                    markerWidth="10"
                    markerHeight="10"
                    refX="8"
                    refY="3"
                    orient="auto"
                    markerUnits="strokeWidth"
                  >
                    <path d="M0,0 L0,6 L9,3 z" fill="#34d399" />
                  </marker>
                  <marker
                    id="arrowOutgoing"
                    markerWidth="10"
                    markerHeight="10"
                    refX="8"
                    refY="3"
                    orient="auto"
                    markerUnits="strokeWidth"
                  >
                    <path d="M0,0 L0,6 L9,3 z" fill="#fbbf24" />
                  </marker>
                  {/* Drop shadow filter */}
                  <filter
                    id="dropShadow"
                    x="-20%"
                    y="-20%"
                    width="140%"
                    height="140%"
                  >
                    <feDropShadow
                      dx="0"
                      dy="4"
                      stdDeviation="6"
                      floodOpacity="0.22"
                    />
                  </filter>
                </defs>

                {/* Render edges first so nodes always stay on top and readable. */}
                {edges.map((edge, idx) => {
                  const isConnected = isEdgeConnectedToHoveredNode(edge);
                  return (
                    <g key={`edge-${idx}`}>
                      <path
                        d={generatePath(edge)}
                        fill="none"
                        stroke={getEdgeColor(edge.type)}
                        strokeWidth={isConnected ? 4.8 : 2.2}
                        strokeLinecap="round"
                        markerEnd={
                          edge.type === 'incoming'
                            ? 'url(#arrowIncoming)'
                            : 'url(#arrowOutgoing)'
                        }
                        opacity={hoveredNodeId ? (isConnected ? 1 : 0.12) : 0.8}
                      />
                    </g>
                  );
                })}

                {/* Render nodes after edges for proper visual hierarchy. */}
                {nodes.map(node => {
                  const radius =
                    node.type === 'center' ? CENTER_NODE_RADIUS : NODE_RADIUS;
                  const nameLines = splitLabelToLines(
                    node.label,
                    node.type === 'center' ? 16 : 12,
                  );
                  const isConnected = isNodeConnectedToHoveredNode(node.id);
                  const isHovered = hoveredNodeId === node.id;
                  return (
                    <g
                      key={node.id}
                      style={{
                        cursor: 'pointer',
                      }}
                      onMouseEnter={() => setHoveredNodeId(node.id)}
                      onMouseLeave={() => setHoveredNodeId(null)}
                    >
                      {/* Node circle */}
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={radius}
                        fill={getNodeGradientId(node.type)}
                        filter="url(#dropShadow)"
                        opacity={hoveredNodeId ? (isConnected ? 1 : 0.3) : 1}
                      />
                      {isHovered && (
                        <circle
                          cx={node.x}
                          cy={node.y}
                          r={radius + 8}
                          fill="none"
                          stroke={
                            node.type === 'incoming'
                              ? '#34d399'
                              : node.type === 'outgoing'
                                ? '#f59e0b'
                                : theme.colors.primary.dark1
                          }
                          strokeWidth="3"
                          opacity="0.9"
                        />
                      )}
                      {/* Node label */}
                      <text
                        x={node.x}
                        y={
                          node.y -
                          ((nameLines.length - 1) *
                            (node.type === 'center' ? 7 : 6)) /
                            2 -
                          2
                        }
                        textAnchor="middle"
                        fontSize={node.type === 'center' ? '11.5' : '10'}
                        fontWeight="600"
                        fill="white"
                        opacity={hoveredNodeId ? (isConnected ? 1 : 0.45) : 1}
                      >
                        {nameLines.map((line, idx) => (
                          <tspan
                            key={`${node.id}-${line}-${idx}`}
                            x={node.x}
                            dy={
                              idx === 0 ? 0 : node.type === 'center' ? 14 : 12
                            }
                          >
                            {line}
                          </tspan>
                        ))}
                      </text>
                    </g>
                  );
                })}

                {/* Hover tooltip for non-center nodes */}
                {(() => {
                  const hoveredNode = nodes.find(
                    n => n.id === hoveredNodeId && n.type !== 'center',
                  );
                  if (!hoveredNode) {
                    return null;
                  }

                  const flowCount = hoveredNode.count || 0;
                  const isFromNode = hoveredNode.type === 'incoming';
                  const middleNodeName = selectedNode.nodeName;

                  // Use unique customer data when in unique customer view mode
                  const fromNodeStats = nodeStatsMap.get(hoveredNode.label);
                  const fromNodeTotal = isFromNode
                    ? (isUniqueCustomerView
                        ? fromNodeStats?.uniqueCustomers
                        : fromNodeStats?.totalVisits) || flowCount
                    : 0;
                  const fromNodeToMiddleShare =
                    isFromNode && fromNodeTotal > 0
                      ? (flowCount / fromNodeTotal) * 100
                      : 0;
                  const middleTotal = selectedNode.totalVisits;
                  const middleShare =
                    middleTotal > 0 ? (flowCount / middleTotal) * 100 : 0;

                  // Terminology changes based on view mode
                  const entityLabel = isUniqueCustomerView
                    ? 'Unique customers'
                    : 'Customers';
                  const totalLabel = isUniqueCustomerView
                    ? 'Unique customers'
                    : 'Total visits';

                  const pathCountLabel = isFromNode
                    ? `${entityLabel} from ${hoveredNode.label} to ${middleNodeName}`
                    : `${entityLabel} from ${middleNodeName} to ${hoveredNode.label}`;
                  const secondaryLabel = isFromNode
                    ? totalLabel
                    : `Share of ${middleNodeName} ${isUniqueCustomerView ? 'unique customers' : 'total visits'}`;
                  const secondaryValue = isFromNode
                    ? fromNodeTotal.toLocaleString()
                    : `${middleShare.toFixed(1)}%`;
                  const pathValue = isFromNode
                    ? `${flowCount.toLocaleString()} (${fromNodeToMiddleShare.toFixed(1)}%)`
                    : flowCount.toLocaleString();
                  const tooltipWidth = 330;

                  // Break long strings into lines for each text area
                  // Header label shares row with the type badge (~80px), so ~20 chars max
                  const headerLines = splitLabelToLines(hoveredNode.label, 20);
                  // Row labels occupy left side only; value is right-anchored; ~38 chars
                  const secondaryLabelLines = splitLabelToLines(
                    secondaryLabel,
                    38,
                  );
                  const pathLabelLines = splitLabelToLines(pathCountLabel, 38);

                  const LINE_H = 13; // px between wrapped label lines
                  const ROW_GAP = 22; // px between distinct rows

                  const headerExtraH = (headerLines.length - 1) * LINE_H;
                  // Header block: base 34px tall, grows with extra label lines
                  const headerBlockH = 34 + headerExtraH;

                  const dividerOffsetY = headerBlockH + 10;
                  const row1OffsetY = dividerOffsetY + ROW_GAP;
                  const row1ExtraH = (secondaryLabelLines.length - 1) * LINE_H;
                  const row2OffsetY = row1OffsetY + ROW_GAP + row1ExtraH;
                  const row2ExtraH = (pathLabelLines.length - 1) * LINE_H;

                  const tooltipHeight = row2OffsetY + row2ExtraH + 18;

                  const preferredX = isFromNode
                    ? hoveredNode.x + 24
                    : hoveredNode.x - tooltipWidth - 24;
                  const tooltipX = Math.min(
                    Math.max(12, preferredX),
                    GRAPH_WIDTH - tooltipWidth - 12,
                  );
                  const tooltipY = Math.min(
                    Math.max(12, hoveredNode.y - tooltipHeight / 2),
                    GRAPH_HEIGHT - tooltipHeight - 12,
                  );

                  return (
                    <g pointerEvents="none">
                      <rect
                        x={tooltipX}
                        y={tooltipY}
                        width={tooltipWidth}
                        height={tooltipHeight}
                        rx="12"
                        fill="rgba(255,255,255,0.98)"
                        stroke={
                          hoveredNode.type === 'incoming'
                            ? '#34d399'
                            : '#fbbf24'
                        }
                        strokeWidth="1.5"
                        filter="url(#dropShadow)"
                      />
                      {/* Tinted header strip — height matches wrapped label */}
                      <rect
                        x={tooltipX}
                        y={tooltipY}
                        width={tooltipWidth}
                        height={headerBlockH}
                        rx="12"
                        fill={
                          hoveredNode.type === 'incoming'
                            ? 'rgba(16,185,129,0.14)'
                            : 'rgba(245,158,11,0.16)'
                        }
                      />
                      {/* Node name — wrapped into multiple tspan lines */}
                      <text
                        x={tooltipX + 12}
                        y={tooltipY + 22}
                        fontSize="12.5"
                        fontWeight="700"
                        fill="#111827"
                      >
                        {headerLines.map((line, i) => (
                          <tspan
                            key={`hl-${i}`}
                            x={tooltipX + 12}
                            dy={i === 0 ? 0 : LINE_H}
                          >
                            {line}
                          </tspan>
                        ))}
                      </text>
                      {/* Type badge pinned to top-right of header */}
                      <text
                        x={tooltipX + tooltipWidth - 12}
                        y={tooltipY + 22}
                        textAnchor="end"
                        fontSize="10.5"
                        fontWeight="600"
                        fill={
                          hoveredNode.type === 'incoming'
                            ? '#047857'
                            : '#b45309'
                        }
                      >
                        {isFromNode ? 'From Node' : 'Next Node'}
                      </text>
                      <line
                        x1={tooltipX + 12}
                        y1={tooltipY + dividerOffsetY}
                        x2={tooltipX + tooltipWidth - 12}
                        y2={tooltipY + dividerOffsetY}
                        stroke="rgba(148,163,184,0.35)"
                      />
                      {/* Row 1 label (may wrap) + right-anchored value on first line */}
                      <text
                        x={tooltipX + 12}
                        y={tooltipY + row1OffsetY}
                        fontSize="10.5"
                        fill="#6b7280"
                      >
                        {secondaryLabelLines.map((line, i) => (
                          <tspan
                            key={`sl-${i}`}
                            x={tooltipX + 12}
                            dy={i === 0 ? 0 : LINE_H}
                          >
                            {line}
                          </tspan>
                        ))}
                      </text>
                      <text
                        x={tooltipX + tooltipWidth - 12}
                        y={tooltipY + row1OffsetY}
                        textAnchor="end"
                        fontSize="11"
                        fontWeight="700"
                        fill="#111827"
                      >
                        {secondaryValue}
                      </text>
                      {/* Row 2 label (may wrap) + right-anchored value on first line */}
                      <text
                        x={tooltipX + 12}
                        y={tooltipY + row2OffsetY}
                        fontSize="10.5"
                        fill="#6b7280"
                      >
                        {pathLabelLines.map((line, i) => (
                          <tspan
                            key={`pl-${i}`}
                            x={tooltipX + 12}
                            dy={i === 0 ? 0 : LINE_H}
                          >
                            {line}
                          </tspan>
                        ))}
                      </text>
                      <text
                        x={tooltipX + tooltipWidth - 12}
                        y={tooltipY + row2OffsetY}
                        textAnchor="end"
                        fontSize="11"
                        fontWeight="700"
                        fill="#111827"
                      >
                        {pathValue}
                      </text>
                    </g>
                  );
                })()}

                {/* Empty state indicators */}
                {selectedNode.incomingFlows.length === 0 && (
                  <g>
                    <circle
                      cx={INCOMING_X}
                      cy={CENTER_Y}
                      r="35"
                      fill="#f0fdf4"
                      stroke="#86efac"
                      strokeWidth="2"
                      strokeDasharray="5,5"
                    />
                    <text
                      x={INCOMING_X}
                      y={CENTER_Y - 5}
                      textAnchor="middle"
                      fontSize="20"
                    >
                      🚀
                    </text>
                    <text
                      x={INCOMING_X}
                      y={CENTER_Y + 16}
                      textAnchor="middle"
                      fontSize="11"
                      fill="#6b7280"
                    >
                      Start
                    </text>
                  </g>
                )}
                {selectedNode.outgoingFlows.length === 0 && (
                  <g>
                    <circle
                      cx={OUTGOING_X}
                      cy={CENTER_Y}
                      r="35"
                      fill="#fefce8"
                      stroke="#fde047"
                      strokeWidth="2"
                      strokeDasharray="5,5"
                    />
                    <text
                      x={OUTGOING_X}
                      y={CENTER_Y - 5}
                      textAnchor="middle"
                      fontSize="20"
                    >
                      🏁
                    </text>
                    <text
                      x={OUTGOING_X}
                      y={CENTER_Y + 16}
                      textAnchor="middle"
                      fontSize="11"
                      fill="#6b7280"
                    >
                      End
                    </text>
                  </g>
                )}
              </svg>
            </TransformComponent>

            <div className="graph-legend">
              <div className="legend-item">
                <div className="legend-dot incoming" />
                <span>From</span>
              </div>
              <div className="legend-item">
                <div className="legend-dot center" />
                <span>Current</span>
              </div>
              <div className="legend-item">
                <div className="legend-dot outgoing" />
                <span>To</span>
              </div>
            </div>

            <div className="drag-hint">🖱️ Drag to pan • Scroll to zoom</div>
          </>
        )}
      </TransformWrapper>
    </GraphContainer>
  );
}
