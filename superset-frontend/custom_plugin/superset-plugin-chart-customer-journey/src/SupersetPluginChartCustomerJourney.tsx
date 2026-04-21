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
import React, { useEffect, useMemo, useState } from 'react';
import { css, styled } from '@superset-ui/core';
import {
  SupersetPluginChartCustomerJourneyProps,
  ProcessedJourney,
  NodeStats,
  JourneyMode,
} from './types';
import FlowGraph from './FlowGraph';

const MODE_CONFIG: Record<
  JourneyMode,
  {
    pathLabel: string;
    nodeChipClass: string;
    flowNodeClass: string;
    modalTitle: string;
    dwellTitle: string;
  }
> = {
  unit: {
    pathLabel: 'Journey Path',
    nodeChipClass: 'node-chip',
    flowNodeClass: 'flow-node',
    modalTitle: 'Full Journey Path',
    dwellTitle: 'Dwell Time per Stop (Click a stop for details)',
  },
  unit_group: {
    pathLabel: 'Grouped Journey Path',
    nodeChipClass: 'node-chip',
    flowNodeClass: 'flow-node',
    modalTitle: 'Full Grouped Journey Path',
    dwellTitle: 'Aggregated Dwell Time per Group (Click a group for details)',
  },
};

const Styles = styled.div`
  ${({ theme }) => css`
    font-family: ${theme.typography.families.sansSerif};
    height: 100%;
    overflow: auto;

    .header {
      padding: ${theme.gridUnit * 4}px;
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
      background: ${theme.colors.grayscale.light5};
    }

    .header-title {
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark2};
      margin: 0 0 ${theme.gridUnit}px 0;
    }

    .header-subtitle {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
      margin: 0;
    }

    .table-wrapper {
      width: 100%;
      min-width: 0;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }

    thead th {
      background: ${theme.colors.grayscale.light4};
      color: ${theme.colors.grayscale.dark1};
      font-weight: ${theme.typography.weights.bold};
      font-size: ${theme.typography.sizes.s}px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: ${theme.gridUnit * 3}px ${theme.gridUnit * 2}px;
      text-align: left;
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
    }

    tbody tr {
      border-bottom: 1px solid ${theme.colors.grayscale.light3};
      transition: background-color 0.15s ease;
    }

    tbody tr:hover {
      background-color: ${theme.colors.grayscale.light5};
    }

    tbody td {
      padding: ${theme.gridUnit * 3}px ${theme.gridUnit * 2}px;
      font-size: ${theme.typography.sizes.m}px;
      color: ${theme.colors.grayscale.dark2};
      vertical-align: middle;
    }

    .rank-cell {
      width: 56px;
      text-align: center;
      white-space: nowrap;
    }

    .rank-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 50%;
      font-weight: ${theme.typography.weights.bold};
      font-size: ${theme.typography.sizes.s}px;
    }

    .rank-1 {
      background: linear-gradient(135deg, #fbbf24, #f59e0b);
      color: #78350f;
    }

    .rank-2 {
      background: linear-gradient(135deg, #cbd5e1, #94a3b8);
      color: #334155;
    }

    .rank-3 {
      background: linear-gradient(135deg, #fdba74, #fb923c);
      color: #7c2d12;
    }

    .rank-default {
      background: ${theme.colors.grayscale.light3};
      color: ${theme.colors.grayscale.dark1};
    }

    .footfall-cell {
      width: 132px;
      white-space: nowrap;
    }

    .footfall-value {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.primary.base};
    }

    .footfall-icon {
      color: ${theme.colors.primary.light1};
    }

    .journey-nodes {
      display: flex;
      width: 100%;
      min-width: 0;
      flex-wrap: wrap;
      align-items: center;
      gap: ${theme.gridUnit}px;
      cursor: pointer;
      padding: ${theme.gridUnit}px;
      border-radius: ${theme.gridUnit}px;
      transition: background-color 0.15s ease;
    }

    .journey-nodes:hover {
      background-color: ${theme.colors.primary.light5};
    }

    .node-chip {
      display: inline-flex;
      align-items: center;
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      background: ${theme.colors.grayscale.light4};
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.dark1};
      font-weight: ${theme.typography.weights.normal};
    }

    .node-arrow {
      color: ${theme.colors.grayscale.base};
      font-size: ${theme.typography.sizes.s}px;
    }

    .more-nodes {
      display: inline-flex;
      align-items: center;
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      background: ${theme.colors.primary.light4};
      border: 1px solid ${theme.colors.primary.light2};
      border-radius: ${theme.gridUnit}px;
      color: ${theme.colors.primary.dark1};
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.medium};
    }

    .click-hint {
      color: ${theme.colors.primary.base};
      font-size: ${theme.typography.sizes.xs}px;
      margin-left: ${theme.gridUnit * 2}px;
      opacity: 0;
      transition: opacity 0.15s ease;
    }

    .journey-nodes:hover .click-hint {
      opacity: 1;
    }

    /* Modal Styles */
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: ${theme.gridUnit * 5}px;
    }

    .modal-content {
      background: ${theme.colors.grayscale.light5};
      border-radius: ${theme.gridUnit * 2}px;
      width: min(96vw, 1120px);
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15);
      position: relative;
    }

    .modal-header {
      padding: ${theme.gridUnit * 4}px ${theme.gridUnit * 8}px
        ${theme.gridUnit * 4}px ${theme.gridUnit * 5}px;
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
      display: flex;
      justify-content: flex-start;
      align-items: center;
      background: ${theme.colors.grayscale.light4};
    }

    .modal-title {
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark2};
      margin: 0;
    }

    .modal-subtitle {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
      margin: ${theme.gridUnit}px 0 0 0;
    }

    .close-btn {
      position: absolute;
      top: ${theme.gridUnit * 2}px;
      right: ${theme.gridUnit * 2}px;
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: 1px solid ${theme.colors.grayscale.light2};
      background: rgba(255, 255, 255, 0.92);
      font-size: 22px;
      color: ${theme.colors.grayscale.dark1};
      cursor: pointer;
      padding: 0;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }

    .close-btn:hover {
      background: white;
      border-color: ${theme.colors.primary.light1};
      color: ${theme.colors.primary.base};
    }

    .modal-body {
      padding: ${theme.gridUnit * 5}px;
    }

    .section {
      margin-bottom: ${theme.gridUnit * 5}px;
    }

    .section:last-child {
      margin-bottom: 0;
    }

    .section-title {
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.base};
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 0 0 ${theme.gridUnit * 3}px 0;
    }

    /* Journey Flow - Horizontal Path Display */
    .journey-flow {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      padding: ${theme.gridUnit * 4}px;
      background: white;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit * 2}px;
      margin-bottom: ${theme.gridUnit * 4}px;
    }

    .flow-node {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      background: ${theme.colors.primary.light4};
      border: 1px solid ${theme.colors.primary.light2};
      border-radius: ${theme.gridUnit * 2}px;
      font-size: ${theme.typography.sizes.m}px;
      color: ${theme.colors.primary.dark1};
      font-weight: ${theme.typography.weights.medium};
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .flow-node:hover {
      background: ${theme.colors.primary.light3};
      border-color: ${theme.colors.primary.base};
      transform: translateY(-2px);
      box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
    }

    .flow-node-number {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: ${theme.colors.primary.base};
      color: white;
      font-size: ${theme.typography.sizes.xs}px;
      font-weight: ${theme.typography.weights.bold};
    }

    .flow-arrow {
      color: ${theme.colors.grayscale.base};
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
    }

    .timeline {
      display: flex;
      flex-direction: column;
      gap: ${theme.gridUnit * 2}px;
    }

    .timeline-item {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 3}px;
      padding: ${theme.gridUnit * 3}px;
      background: white;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .timeline-item:hover {
      border-color: ${theme.colors.primary.light1};
      background: ${theme.colors.primary.light5};
    }

    .timeline-number {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: ${theme.colors.primary.base};
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.bold};
      flex-shrink: 0;
    }

    .timeline-name {
      flex: 1;
      font-weight: ${theme.typography.weights.medium};
      color: ${theme.colors.grayscale.dark2};
    }

    .timeline-dwell {
      background: ${theme.colors.warning.light2};
      color: ${theme.colors.warning.dark2};
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.medium};
    }

    /* Node Stats Styles */
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: ${theme.gridUnit * 3}px;
      margin-bottom: ${theme.gridUnit * 5}px;
    }

    .stat-card {
      background: white;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      padding: ${theme.gridUnit * 4}px;
      text-align: center;
    }

    .stat-value {
      font-size: ${theme.typography.sizes.xxl}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.primary.base};
      margin-bottom: ${theme.gridUnit}px;
    }

    .stat-label {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    /* Node Graph Styles - Enhanced with Connections */
    .node-graph-container {
      display: flex;
      justify-content: center;
      align-items: center;
      padding: ${theme.gridUnit * 4}px;
      min-height: 320px;
      background: linear-gradient(
        135deg,
        ${theme.colors.grayscale.light5} 0%,
        white 100%
      );
      border-radius: ${theme.gridUnit * 2}px;
      margin: ${theme.gridUnit * 3}px 0;
      position: relative;
      border: 1px solid ${theme.colors.grayscale.light3};
    }

    .graph-flow-wrapper {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0;
      width: 100%;
      position: relative;
    }

    .graph-section {
      display: flex;
      flex-direction: column;
      align-items: center;
      flex: 1;
      max-width: 180px;
      position: relative;
      z-index: 2;
    }

    .graph-section.center {
      flex: 0 0 auto;
      max-width: none;
      margin: 0 ${theme.gridUnit * 2}px;
    }

    .graph-section-label {
      font-size: ${theme.typography.sizes.xs}px;
      color: ${theme.colors.grayscale.base};
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: ${theme.gridUnit * 2}px;
      font-weight: ${theme.typography.weights.bold};
    }

    .graph-nodes-list {
      display: flex;
      flex-direction: column;
      gap: ${theme.gridUnit * 3}px;
      width: 100%;
      align-items: center;
      position: relative;
      padding: ${theme.gridUnit}px 0;
    }

    .graph-nodes-list.incoming {
      align-items: flex-end;
      padding-right: ${theme.gridUnit * 4}px;
    }

    .graph-nodes-list.outgoing {
      align-items: flex-start;
      padding-left: ${theme.gridUnit * 4}px;
    }

    .graph-nodes-list.incoming::before,
    .graph-nodes-list.outgoing::before {
      content: '';
      position: absolute;
      top: 36px;
      bottom: 36px;
      width: 2px;
      border-radius: 2px;
      opacity: 0.8;
    }

    .graph-nodes-list.incoming::before {
      right: ${theme.gridUnit * 2}px;
      background: linear-gradient(180deg, #6ee7b7 0%, #34d399 100%);
    }

    .graph-nodes-list.outgoing::before {
      left: ${theme.gridUnit * 2}px;
      background: linear-gradient(180deg, #fde68a 0%, #fbbf24 100%);
    }

    .graph-node-row {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      width: 100%;
    }

    .graph-node {
      display: flex;
      flex-direction: column;
      align-items: center;
      position: relative;
      width: 100%;
      max-width: 140px;
    }

    .graph-node.incoming,
    .graph-node.outgoing {
      align-items: center;
    }

    .graph-node.incoming::after,
    .graph-node.outgoing::before {
      content: '';
      position: absolute;
      top: 34px;
      width: ${theme.gridUnit * 2}px;
      height: 2px;
      border-radius: 2px;
      opacity: 0.9;
    }

    .graph-node.incoming::after {
      right: -${theme.gridUnit * 2}px;
      background: #34d399;
    }

    .graph-node.outgoing::before {
      left: -${theme.gridUnit * 2}px;
      background: #fbbf24;
    }

    .node-circle {
      width: 70px;
      height: 70px;
      border-radius: 50%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      font-weight: ${theme.typography.weights.bold};
      color: white;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      transition: all 0.25s ease;
      position: relative;
      z-index: 2;
      cursor: default;
    }

    .node-circle:hover {
      transform: scale(1.08);
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
    }

    .node-circle-incoming {
      background: linear-gradient(135deg, #34d399 0%, #059669 100%);
    }

    .node-circle-center {
      background: linear-gradient(
        135deg,
        ${theme.colors.primary.light1} 0%,
        ${theme.colors.primary.dark1} 100%
      );
      width: 90px;
      height: 90px;
      font-size: ${theme.typography.sizes.m}px;
      box-shadow: 0 6px 24px rgba(0, 0, 0, 0.2);
    }

    .node-circle-outgoing {
      background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);
    }

    .node-name {
      font-size: ${theme.typography.sizes.s}px;
      color: white;
      text-align: center;
      word-break: break-word;
      padding: ${theme.gridUnit}px;
      line-height: 1.1;
      max-width: 60px;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .node-circle-center .node-name {
      max-width: 75px;
      font-size: ${theme.typography.sizes.m}px;
    }

    .node-label {
      font-size: ${theme.typography.sizes.xs}px;
      color: ${theme.colors.grayscale.base};
      margin-top: ${theme.gridUnit}px;
      text-align: center;
      display: flex;
      align-items: center;
      flex-direction: column;
    }

    .node-label-center {
      margin-top: ${theme.gridUnit * 2}px;
    }

    .node-visits {
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark1};
    }

    .node-percentage {
      font-size: ${theme.typography.sizes.xs}px;
      color: ${theme.colors.grayscale.base};
      margin-top: 2px;
    }

    /* Connection Arrows */
    .connection-arrows {
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      height: 100%;
      position: relative;
      z-index: 1;
      min-width: 60px;
    }

    .arrow-line {
      position: relative;
      height: 3px;
      width: 50px;
      display: flex;
      align-items: center;
      margin: ${theme.gridUnit * 4}px 0;
    }

    .arrow-line.incoming {
      background: linear-gradient(90deg, #34d399, #34d399 70%, transparent 70%);
    }

    .arrow-line.outgoing {
      background: linear-gradient(
        90deg,
        transparent 0,
        transparent 30%,
        #fbbf24 30%
      );
    }

    .arrow-line::after {
      content: '';
      position: absolute;
      right: 0;
      width: 0;
      height: 0;
      border-style: solid;
    }

    .arrow-line.incoming::after {
      border-width: 6px 0 6px 10px;
      border-color: transparent transparent transparent #34d399;
    }

    .arrow-line.outgoing::after {
      border-width: 6px 0 6px 10px;
      border-color: transparent transparent transparent #fbbf24;
    }

    .arrow-flow-count {
      position: absolute;
      top: -18px;
      left: 50%;
      transform: translateX(-50%);
      font-size: ${theme.typography.sizes.xs}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark1};
      white-space: nowrap;
      background: white;
      padding: 2px 6px;
      border-radius: 10px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
    }

    .empty-flow-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: ${theme.colors.grayscale.light1};
      font-style: italic;
      font-size: ${theme.typography.sizes.s}px;
      text-align: center;
      padding: ${theme.gridUnit * 2}px;
      min-height: 150px;
    }

    .empty-flow-icon {
      font-size: 32px;
      margin-bottom: ${theme.gridUnit}px;
      opacity: 0.5;
    }

    .flow-list {
      display: flex;
      flex-direction: column;
      gap: ${theme.gridUnit * 2}px;
    }

    .flow-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      background: white;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
    }

    .flow-name {
      font-weight: ${theme.typography.weights.medium};
      color: ${theme.colors.grayscale.dark2};
    }

    .flow-stats {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 3}px;
    }

    .flow-count {
      color: ${theme.colors.grayscale.base};
      font-size: ${theme.typography.sizes.s}px;
    }

    .flow-pct-incoming {
      color: ${theme.colors.success.base};
      font-weight: ${theme.typography.weights.bold};
    }

    .flow-pct-outgoing {
      color: ${theme.colors.warning.dark1};
      font-weight: ${theme.typography.weights.bold};
    }

    .back-btn {
      background: transparent;
      border: 1px solid ${theme.colors.grayscale.light1};
      color: ${theme.colors.grayscale.dark1};
      padding: ${theme.gridUnit}px ${theme.gridUnit * 3}px;
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.s}px;
      cursor: pointer;
      margin-bottom: ${theme.gridUnit * 4}px;
      transition: all 0.15s ease;
    }

    .back-btn:hover {
      background: ${theme.colors.grayscale.light4};
    }

    .empty-state {
      text-align: center;
      padding: ${theme.gridUnit * 4}px;
      color: ${theme.colors.grayscale.base};
      font-style: italic;
    }

    /* Pagination Styles */
    .pagination-container {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: ${theme.gridUnit * 3}px ${theme.gridUnit * 4}px;
      border-top: 1px solid ${theme.colors.grayscale.light2};
      background: ${theme.colors.grayscale.light5};
    }

    .pagination-info {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
    }

    .pagination-controls {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
    }

    .pagination-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 32px;
      height: 32px;
      padding: 0 ${theme.gridUnit * 2}px;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      background: white;
      color: ${theme.colors.grayscale.dark1};
      font-size: ${theme.typography.sizes.s}px;
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .pagination-btn:hover:not(:disabled) {
      background: ${theme.colors.grayscale.light4};
      border-color: ${theme.colors.grayscale.light1};
    }

    .pagination-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .pagination-btn.active {
      background: ${theme.colors.primary.base};
      border-color: ${theme.colors.primary.base};
      color: white;
    }

    .pagination-btn.active:hover {
      background: ${theme.colors.primary.dark1};
    }

    .page-size-selector {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
    }

    .page-size-selector label {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
    }

    .page-size-select {
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      background: white;
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.dark1};
      cursor: pointer;
    }

    /* Mode Header Styles */
    .mode-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: ${theme.gridUnit * 3}px ${theme.gridUnit * 4}px;
      background: ${theme.colors.grayscale.light5};
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
    }

    .mode-title {
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark2};
      margin: 0;
    }

    .mode-badge {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      border-radius: ${theme.gridUnit * 2}px;
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.medium};
    }

    .mode-badge-unit {
      background: ${theme.colors.info.light2};
      color: ${theme.colors.info.dark2};
      border: 1px solid ${theme.colors.info.light1};
    }

    .mode-badge-unit-group {
      background: ${theme.colors.success.light2};
      color: ${theme.colors.success.dark2};
      border: 1px solid ${theme.colors.success.light1};
    }

    .mode-icon {
      width: 14px;
      height: 14px;
    }

    /* Node chip variations by mode */
    .node-chip-grouped {
      background: ${theme.colors.success.light2};
      border-color: ${theme.colors.success.light1};
      color: ${theme.colors.success.dark1};
    }

    .grouped-indicator {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      margin-left: ${theme.gridUnit * 2}px;
      padding: 2px ${theme.gridUnit}px;
      background: ${theme.colors.success.light1};
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.xs}px;
      color: ${theme.colors.success.dark1};
    }

    /* Flow node variations by mode */
    .flow-node-grouped {
      background: ${theme.colors.success.light2};
      border-color: ${theme.colors.success.light1};
      color: ${theme.colors.success.dark1};
    }

    .flow-node-grouped:hover {
      background: ${theme.colors.success.light1};
      border-color: ${theme.colors.success.base};
    }

    .flow-node-grouped .flow-node-number {
      background: ${theme.colors.success.base};
    }

    /* Mode description */
    .mode-description {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
      margin-top: ${theme.gridUnit}px;
      font-style: italic;
    }

    /* Search Bar Styles - Multi-select style */
    .search-section {
      background: ${theme.colors.grayscale.light5};
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
      padding: ${theme.gridUnit * 3}px;
    }

    .search-container {
      position: relative;
    }

    .search-select-box {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      min-height: 40px;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      background: white;
      cursor: text;
      transition: all 0.15s ease;
    }

    .search-select-box:focus-within {
      border-color: ${theme.colors.primary.base};
      box-shadow: 0 0 0 2px ${theme.colors.primary.light4};
    }

    .search-tag {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      background: ${theme.colors.grayscale.light4};
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.dark1};
      font-weight: ${theme.typography.weights.medium};
    }

    .search-tag-remove {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: transparent;
      border: none;
      color: ${theme.colors.grayscale.base};
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 0;
      transition: all 0.1s ease;
    }

    .search-tag-remove:hover {
      background: ${theme.colors.grayscale.light3};
      color: ${theme.colors.grayscale.dark1};
    }

    .search-input-inline {
      flex: 1;
      min-width: 120px;
      border: none;
      outline: none;
      font-size: ${theme.typography.sizes.m}px;
      color: ${theme.colors.grayscale.dark2};
      background: transparent;
      padding: ${theme.gridUnit}px 0;
    }

    .search-input-inline::placeholder {
      color: ${theme.colors.grayscale.light1};
    }

    .search-results {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      margin-top: ${theme.gridUnit}px;
      background: white;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
      max-height: 280px;
      overflow-y: auto;
      z-index: 100;
    }

    .search-result-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      cursor: pointer;
      transition: background-color 0.1s ease;
      border-bottom: 1px solid ${theme.colors.grayscale.light3};
    }

    .search-result-item:last-child {
      border-bottom: none;
    }

    .search-result-item:hover {
      background: ${theme.colors.primary.light5};
    }

    .search-result-item.selected {
      background: ${theme.colors.grayscale.light4};
    }

    .search-result-item.active {
      background: ${theme.colors.primary.light5};
      outline: 1px solid ${theme.colors.primary.light2};
    }

    .search-result-name {
      font-weight: ${theme.typography.weights.medium};
      color: ${theme.colors.grayscale.dark2};
    }

    .search-result-stats {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
    }

    .search-result-visits {
      color: ${theme.colors.primary.base};
      font-weight: ${theme.typography.weights.bold};
    }

    .search-no-results {
      padding: ${theme.gridUnit * 3}px;
      text-align: center;
      color: ${theme.colors.grayscale.base};
      font-style: italic;
    }

    .search-hint {
      font-size: ${theme.typography.sizes.xs}px;
      color: ${theme.colors.grayscale.light1};
      margin-top: ${theme.gridUnit}px;
    }

    /* Selected Items Panel */
    .selected-items-panel {
      margin-top: ${theme.gridUnit * 2}px;
      padding: ${theme.gridUnit * 2}px;
      background: white;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
    }

    .selected-items-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: ${theme.gridUnit * 2}px;
    }

    .selected-items-title {
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark1};
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .clear-all-btn {
      background: transparent;
      border: none;
      color: ${theme.colors.primary.base};
      font-size: ${theme.typography.sizes.s}px;
      cursor: pointer;
      padding: ${theme.gridUnit}px;
    }

    .clear-all-btn:hover {
      text-decoration: underline;
    }

    .selected-item-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 2}px;
      background: ${theme.colors.grayscale.light5};
      border-radius: ${theme.gridUnit}px;
      margin-bottom: ${theme.gridUnit}px;
    }

    .selected-item-row:last-child {
      margin-bottom: 0;
    }

    .selected-item-info {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
    }

    .selected-item-name {
      font-weight: ${theme.typography.weights.medium};
      color: ${theme.colors.grayscale.dark2};
    }

    .selected-item-stats {
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.grayscale.base};
    }

    .selected-item-actions {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
    }

    .view-btn {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit}px ${theme.gridUnit * 2}px;
      background: ${theme.colors.primary.base};
      color: white;
      border: none;
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.medium};
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .view-btn:hover {
      background: ${theme.colors.primary.dark1};
    }

    .remove-item-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      background: transparent;
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit}px;
      color: ${theme.colors.grayscale.base};
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 0;
      transition: all 0.1s ease;
    }

    .remove-item-btn:hover {
      background: ${theme.colors.error.light2};
      border-color: ${theme.colors.error.light1};
      color: ${theme.colors.error.dark1};
    }

    /* Spacer between search and table */
    .section-spacer {
      height: ${theme.gridUnit * 3}px;
      background: ${theme.colors.grayscale.light4};
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
    }

    @media (max-width: 900px) {
      .mode-header {
        padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      }

      thead th,
      tbody td {
        padding: ${theme.gridUnit * 2}px ${theme.gridUnit}px;
      }

      .footfall-cell {
        width: 112px;
      }

      .footfall-value {
        gap: ${theme.gridUnit}px;
      }

      .node-chip,
      .more-nodes {
        padding: 2px ${theme.gridUnit}px;
      }
    }

    @media (max-width: 640px) {
      .mode-header {
        justify-content: flex-start;
      }

      .click-hint {
        display: none;
      }

      .rank-cell {
        width: 46px;
      }

      .footfall-cell {
        width: 96px;
      }

      .footfall-value {
        font-size: ${theme.typography.sizes.s}px;
      }

      .footfall-icon {
        width: 14px;
        height: 14px;
      }
    }
  `}
`;

export default function SupersetPluginChartCustomerJourney(
  props: SupersetPluginChartCustomerJourneyProps,
) {
  const { height, width, processedData, journeyMode } = props;

  const [selectedJourney, setSelectedJourney] =
    useState<ProcessedJourney | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeStats | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [selectedItems, setSelectedItems] = useState<NodeStats[]>([]);
  const [activeSearchIndex, setActiveSearchIndex] = useState(0);

  const allNodes = useMemo(() => {
    const nodeStats = processedData?.nodeStats;
    if (!nodeStats) return [];
    return Array.from(nodeStats.values()).sort(
      (a, b) => b.totalVisits - a.totalVisits,
    );
  }, [processedData]);

  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const query = searchQuery.toLowerCase();
    return allNodes
      .filter(node => node.nodeName.toLowerCase().includes(query))
      .slice(0, 10);
  }, [allNodes, searchQuery]);

  const selectedItemNames = useMemo(
    () => new Set(selectedItems.map(item => item.nodeName)),
    [selectedItems],
  );

  const topJourneys = useMemo(() => {
    return (processedData?.journeys || []).slice(0, 10);
  }, [processedData]);

  useEffect(() => {
    setSelectedJourney(null);
    setSelectedNode(null);
    setSearchQuery('');
    setSelectedItems([]);
    setActiveSearchIndex(0);
  }, [journeyMode, processedData]);

  useEffect(() => {
    if (!selectedJourney && !selectedNode) {
      return undefined;
    }

    const handleEscClose = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelectedJourney(null);
        setSelectedNode(null);
      }
    };

    window.addEventListener('keydown', handleEscClose);
    return () => window.removeEventListener('keydown', handleEscClose);
  }, [selectedJourney, selectedNode]);

  useEffect(() => {
    if (!searchQuery.trim() || searchResults.length === 0) {
      setActiveSearchIndex(0);
      return;
    }
    setActiveSearchIndex(prev => Math.min(prev, searchResults.length - 1));
  }, [searchQuery, searchResults]);

  const handleSearchResultClick = (node: NodeStats) => {
    setSelectedItems(prev =>
      prev.some(item => item.nodeName === node.nodeName)
        ? prev
        : [...prev, node],
    );
    setSearchQuery('');
    setIsSearchFocused(false);
    setActiveSearchIndex(0);
  };

  const handleSearchInputKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
  ) => {
    if (!isSearchFocused || !searchQuery.trim() || searchResults.length === 0) {
      if (event.key === 'Escape') {
        setIsSearchFocused(false);
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveSearchIndex(prev =>
        prev >= searchResults.length - 1 ? 0 : prev + 1,
      );
      return;
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveSearchIndex(prev =>
        prev <= 0 ? searchResults.length - 1 : prev - 1,
      );
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      const candidate =
        searchResults[activeSearchIndex] ||
        searchResults.find(
          node =>
            node.nodeName.toLowerCase() === searchQuery.trim().toLowerCase(),
        ) ||
        searchResults[0];
      if (candidate) {
        handleSearchResultClick(candidate);
      }
      return;
    }

    if (event.key === 'Escape') {
      event.preventDefault();
      setIsSearchFocused(false);
    }
  };

  const handleRemoveSelectedItem = (nodeName: string) => {
    setSelectedItems(prev => prev.filter(item => item.nodeName !== nodeName));
  };

  const handleClearAllSelected = () => {
    setSelectedItems([]);
  };

  const handleViewItem = (node: NodeStats) => {
    setSelectedNode(node);
    setSelectedJourney(null);
  };

  const handleJourneyClick = (journey: ProcessedJourney) => {
    setSelectedJourney(journey);
    setSelectedNode(null);
  };

  const handleNodeClick = (nodeName: string) => {
    const stats = processedData?.nodeStats?.get(nodeName);
    if (stats) {
      setSelectedNode(stats);
    }
  };

  const handleCloseModal = () => {
    setSelectedJourney(null);
    setSelectedNode(null);
  };

  const handleBackToJourney = () => {
    setSelectedNode(null);
  };

  const formatDwellTime = (mins: number) => {
    if (mins >= 60) {
      const hours = Math.floor(mins / 60);
      const remaining = Math.round(mins % 60);
      return `${hours}h ${remaining}m`;
    }
    return `${mins.toFixed(1)} min`;
  };

  const getRankClass = (rank: number) => {
    if (rank === 1) return 'rank-badge rank-1';
    if (rank === 2) return 'rank-badge rank-2';
    if (rank === 3) return 'rank-badge rank-3';
    return 'rank-badge rank-default';
  };

  const isGroupMode = journeyMode === 'unit_group';
  const modeConfig = MODE_CONFIG[journeyMode];
  const itemLabelPlural = isGroupMode ? 'groups' : 'locations';

  return (
    <Styles style={{ height, width }}>
      {/* Search Section */}
      <div className="search-section">
        <div className="search-container">
          <div
            className="search-select-box"
            onClick={() => setIsSearchFocused(true)}
          >
            {selectedItems.map(item => (
              <span key={item.nodeName} className="search-tag">
                {item.nodeName}
                <button
                  className="search-tag-remove"
                  onClick={e => {
                    e.stopPropagation();
                    handleRemoveSelectedItem(item.nodeName);
                  }}
                >
                  ×
                </button>
              </span>
            ))}
            <input
              type="text"
              className="search-input-inline"
              placeholder={
                selectedItems.length === 0
                  ? isGroupMode
                    ? 'Search and select groups...'
                    : 'Search and select locations...'
                  : 'Add more...'
              }
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchInputKeyDown}
              onFocus={() => setIsSearchFocused(true)}
              onBlur={() => setTimeout(() => setIsSearchFocused(false), 200)}
            />
          </div>
          {isSearchFocused && searchQuery.trim() && (
            <div className="search-results">
              {searchResults.length > 0 ? (
                searchResults.map((node, index) => (
                  <div
                    key={node.nodeName}
                    className={`search-result-item ${
                      activeSearchIndex === index ? 'active' : ''
                    } ${
                      selectedItemNames.has(node.nodeName) ? 'selected' : ''
                    }`}
                    onClick={() => handleSearchResultClick(node)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleSearchResultClick(node);
                    }}
                  >
                    <span className="search-result-name">{node.nodeName}</span>
                    <div className="search-result-stats">
                      <span className="search-result-visits">
                        {node.totalVisits.toLocaleString()} visits
                      </span>
                      <span>•</span>
                      <span>{formatDwellTime(node.avgDwellTime)} avg</span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="search-no-results">
                  No {itemLabelPlural} found for "{searchQuery}"
                </div>
              )}
            </div>
          )}
          <div className="search-hint">
            Select {itemLabelPlural} to view their statistics
          </div>
        </div>

        {/* Selected Items Panel */}
        {selectedItems.length > 0 && (
          <div className="selected-items-panel">
            <div className="selected-items-header">
              <span className="selected-items-title">
                Selected {isGroupMode ? 'Groups' : 'Locations'} (
                {selectedItems.length})
              </span>
              <button
                className="clear-all-btn"
                onClick={handleClearAllSelected}
              >
                Clear All
              </button>
            </div>
            {selectedItems.map(item => (
              <div key={item.nodeName} className="selected-item-row">
                <div className="selected-item-info">
                  <span className="selected-item-name">{item.nodeName}</span>
                  <span className="selected-item-stats">
                    {item.totalVisits.toLocaleString()} visits •{' '}
                    {formatDwellTime(item.avgDwellTime)} avg
                  </span>
                </div>
                <div className="selected-item-actions">
                  <button
                    className="view-btn"
                    onClick={() => handleViewItem(item)}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="currentColor"
                    >
                      <path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z" />
                    </svg>
                    View
                  </button>
                  <button
                    className="remove-item-btn"
                    onClick={() => handleRemoveSelectedItem(item.nodeName)}
                  >
                    ×
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Spacer */}
      <div className="section-spacer" />

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th className="rank-cell">Rank</th>
              <th className="footfall-cell">Total Footfall</th>
              <th>{modeConfig.pathLabel}</th>
            </tr>
          </thead>
          <tbody>
            {topJourneys.map((journey, index) => (
              <tr key={journey.journeyNodes}>
                <td className="rank-cell">
                  <span className={getRankClass(index + 1)}>{index + 1}</span>
                </td>
                <td className="footfall-cell">
                  <div className="footfall-value">
                    <svg
                      className="footfall-icon"
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="currentColor"
                    >
                      <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
                    </svg>
                    {journey.totalFootfall.toLocaleString()}
                  </div>
                </td>
                <td>
                  <div
                    className="journey-nodes"
                    onClick={() => handleJourneyClick(journey)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleJourneyClick(journey);
                    }}
                  >
                    {journey.nodesList.slice(0, 3).map((node, nodeIndex) => (
                      <React.Fragment key={`${node}-${nodeIndex}`}>
                        {nodeIndex > 0 && <span className="node-arrow">→</span>}
                        <span className={modeConfig.nodeChipClass}>{node}</span>
                      </React.Fragment>
                    ))}
                    {journey.nodesList.length > 3 && (
                      <span className="more-nodes">
                        +{journey.nodesList.length - 3}
                      </span>
                    )}
                    <span className="click-hint">Click to view full path</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Journey Details Modal */}
      {selectedJourney && !selectedNode && (
        <div className="modal-overlay" onClick={handleCloseModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <button className="close-btn" onClick={handleCloseModal}>
                ×
              </button>
            </div>
            <div className="modal-body">
              <div className="section">
                <h4 className="section-title">{modeConfig.modalTitle}</h4>
                <div className="journey-flow">
                  {selectedJourney.nodesList.map((node, index) => (
                    <React.Fragment key={`flow-${node}-${index}`}>
                      {index > 0 && <span className="flow-arrow">→</span>}
                      <div
                        className={modeConfig.flowNodeClass}
                        onClick={() => handleNodeClick(node)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={e => {
                          if (e.key === 'Enter') handleNodeClick(node);
                        }}
                      >
                        <span className="flow-node-number">{index + 1}</span>
                        <span>{node}</span>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>

              <div className="section">
                <h4 className="section-title">{modeConfig.dwellTitle}</h4>
                <div className="timeline">
                  {selectedJourney.nodesList.map((node, index) => (
                    <div
                      key={`${node}-${index}`}
                      className="timeline-item"
                      onClick={() => handleNodeClick(node)}
                    >
                      <div className="timeline-number">{index + 1}</div>
                      <div className="timeline-name">{node}</div>
                      <div className="timeline-dwell">
                        {formatDwellTime(
                          selectedJourney.avgDwellMins[index] || 0,
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Node Stats Modal */}
      {selectedNode && (
        <div className="modal-overlay" onClick={handleCloseModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h3 className="modal-title">{selectedNode.nodeName}</h3>
                <p className="modal-subtitle">
                  {isGroupMode ? 'Group Statistics' : 'Location Statistics'}
                </p>
              </div>
              <button className="close-btn" onClick={handleCloseModal}>
                ×
              </button>
            </div>
            <div className="modal-body">
              {selectedJourney && (
                <button className="back-btn" onClick={handleBackToJourney}>
                  ← Back to Journey
                </button>
              )}

              <div className="stats-grid">
                <div className="stat-card">
                  <div className="stat-value">
                    {selectedNode.totalVisits.toLocaleString()}
                  </div>
                  <div className="stat-label">Total Visits</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">
                    {formatDwellTime(selectedNode.avgDwellTime)}
                  </div>
                  <div className="stat-label">Avg. Dwell Time</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">
                    {selectedNode.firstNodePercentage.toFixed(1)}%
                  </div>
                  <div className="stat-label">
                    {isGroupMode ? 'Starting Group' : 'Starting Point'}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">
                    {selectedNode.lastNodePercentage.toFixed(1)}%
                  </div>
                  <div className="stat-label">
                    {isGroupMode ? 'Ending Group' : 'Ending Point'}
                  </div>
                </div>
              </div>

              <div className="section">
                <h4 className="section-title">Customer Journey Flow</h4>
                <FlowGraph
                  selectedNode={selectedNode}
                  formatDwellTime={formatDwellTime}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </Styles>
  );
}
