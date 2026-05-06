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
import React from 'react';
import { css, styled } from '@superset-ui/core';
import { ProcessedJourney } from '../types';
import PersonIcon from './PersonIcon';

const Styles = styled.div`
  ${({ theme }) => css`
    flex: 1;
    width: 100%;
    min-width: 0;
    min-height: 0;
    overflow-x: auto;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;

    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }

    thead th {
      position: sticky;
      top: 0;
      z-index: 2;
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

    .count-cell {
      width: 132px;
      white-space: nowrap;
    }

    .count-value {
      display: flex;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.primary.base};
    }

    .count-icon {
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
      background: ${theme.colors.grayscale.light3};
      border-radius: ${theme.gridUnit}px;
      color: ${theme.colors.primary.base};
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

    .empty-state {
      text-align: center;
      padding: ${theme.gridUnit * 4}px;
      color: ${theme.colors.grayscale.base};
      font-style: italic;
    }

    @media (max-width: 640px) {
      .click-hint {
        display: none;
      }

      .rank-cell {
        width: 46px;
      }

      .count-cell {
        width: 96px;
      }

      .count-value {
        font-size: ${theme.typography.sizes.s}px;
      }

      .count-icon {
        width: 14px;
        height: 14px;
      }
    }
  `}
`;

const RANK_CLASS_MAP: Record<number, string> = {
  1: 'rank-badge rank-1',
  2: 'rank-badge rank-2',
  3: 'rank-badge rank-3',
};

function getRankClass(rank: number): string {
  return RANK_CLASS_MAP[rank] ?? 'rank-badge rank-default';
}

const PREVIEW_NODE_COUNT = 3;

interface JourneyTableProps {
  journeys: ProcessedJourney[];
  onJourneyClick: (journey: ProcessedJourney) => void;
}

export default function JourneyTable({
  journeys,
  onJourneyClick,
}: JourneyTableProps) {
  return (
    <Styles>
      <table>
        <thead>
          <tr>
            <th className="rank-cell">Rank</th>
            <th className="count-cell">Journey Count</th>
            <th>Journey Path</th>
          </tr>
        </thead>
        <tbody>
          {journeys.length === 0 ? (
            <tr>
              <td colSpan={3} className="empty-state">
                No journeys match the current filter.
              </td>
            </tr>
          ) : (
            journeys.map((journey, index) => (
              <tr key={`journey-${journey.journeyNodes}`}>
                <td className="rank-cell">
                  <span className={getRankClass(index + 1)}>{index + 1}</span>
                </td>
                <td className="count-cell">
                  <div className="count-value">
                    <PersonIcon className="count-icon" />
                    {journey.journeyCount.toLocaleString()}
                  </div>
                </td>
                <td>
                  <div
                    className="journey-nodes"
                    onClick={() => onJourneyClick(journey)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={e => {
                      if (e.key === 'Enter') onJourneyClick(journey);
                    }}
                  >
                    {journey.nodesList
                      .slice(0, PREVIEW_NODE_COUNT)
                      .map((node, nodeIndex) => (
                        // eslint-disable-next-line react/no-array-index-key
                        <React.Fragment key={`${node}-${nodeIndex}`}>
                          {nodeIndex > 0 && (
                            <span className="node-arrow">→</span>
                          )}
                          <span className="node-chip">{node}</span>
                        </React.Fragment>
                      ))}
                    {journey.nodesList.length > PREVIEW_NODE_COUNT && (
                      <span className="more-nodes">
                        +{journey.nodesList.length - PREVIEW_NODE_COUNT}
                      </span>
                    )}
                    <span className="click-hint">Click to view full path</span>
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </Styles>
  );
}
