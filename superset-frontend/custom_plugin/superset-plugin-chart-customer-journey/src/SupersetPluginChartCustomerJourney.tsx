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
import { SupersetPluginChartCustomerJourneyProps, ProcessedJourney } from './types';

function normalizeSearchValue(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function getEditDistance(source: string, target: string): number {
  const matrix = Array.from({ length: source.length + 1 }, () =>
    Array<number>(target.length + 1).fill(0),
  );

  for (let sourceIndex = 0; sourceIndex <= source.length; sourceIndex += 1) {
    matrix[sourceIndex][0] = sourceIndex;
  }

  for (let targetIndex = 0; targetIndex <= target.length; targetIndex += 1) {
    matrix[0][targetIndex] = targetIndex;
  }

  for (let sourceIndex = 1; sourceIndex <= source.length; sourceIndex += 1) {
    for (let targetIndex = 1; targetIndex <= target.length; targetIndex += 1) {
      const cost = source[sourceIndex - 1] === target[targetIndex - 1] ? 0 : 1;

      matrix[sourceIndex][targetIndex] = Math.min(
        matrix[sourceIndex - 1][targetIndex] + 1,
        matrix[sourceIndex][targetIndex - 1] + 1,
        matrix[sourceIndex - 1][targetIndex - 1] + cost,
      );
    }
  }

  return matrix[source.length][target.length];
}

function matchesSearchQuery(node: string, query: string): boolean {
  const normalizedNode = normalizeSearchValue(node);
  const normalizedQuery = normalizeSearchValue(query);

  if (!normalizedQuery) {
    return true;
  }

  if (normalizedNode.includes(normalizedQuery)) {
    return true;
  }

  const queryTokens = normalizedQuery.split(' ').filter(Boolean);
  const nodeTokens = normalizedNode.split(' ').filter(Boolean);

  return queryTokens.every(queryToken =>
    nodeTokens.some(nodeToken => {
      if (nodeToken.startsWith(queryToken) || nodeToken.includes(queryToken)) {
        return true;
      }

      if (
        queryToken.length >= 4 &&
        Math.abs(nodeToken.length - queryToken.length) <= 1
      ) {
        return getEditDistance(nodeToken, queryToken) <= 1;
      }

      return false;
    }),
  );
}

const Styles = styled.div`
  ${({ theme }) => css`
    font-family: ${theme.typography.families.sansSerif};
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;

    .table-wrapper {
      flex: 1;
      width: 100%;
      min-width: 0;
      min-height: 0;
      overflow-x: auto;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
    }

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
      background: ${theme.colors.grayscale.light3};
      border-radius: ${theme.gridUnit}px;
      color: ${theme.colors.grayscale.dark1};
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
      width: min(96vw, 900px);
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.15);
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
      z-index: 2;
    }

    .close-btn:hover {
      background: white;
      border-color: ${theme.colors.primary.light1};
      color: ${theme.colors.primary.base};
    }

    .modal-body {
      padding: ${theme.gridUnit * 5}px;
    }

    .footfall-pill {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      background: ${theme.colors.primary.light5};
      border: 1px solid ${theme.colors.primary.light2};
      border-radius: ${theme.gridUnit * 2}px;
      color: ${theme.colors.primary.dark1};
      font-size: ${theme.typography.sizes.m}px;
      font-weight: ${theme.typography.weights.bold};
      margin-bottom: ${theme.gridUnit * 4}px;
    }

    /* Journey Flow Path - Beautiful Display */
    .journey-path {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      padding: ${theme.gridUnit * 4}px;
      background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit * 2}px;
    }

    .path-node {
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
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }

    .path-node-number {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: ${theme.colors.primary.base};
      color: white;
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.bold};
    }

    .path-connector {
      color: ${theme.colors.grayscale.base};
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
    }

    /* Search Bar Styles */
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
      background: ${theme.colors.primary.light4};
      border: 1px solid ${theme.colors.primary.light2};
      border-radius: ${theme.gridUnit}px;
      font-size: ${theme.typography.sizes.s}px;
      color: ${theme.colors.primary.dark1};
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
      color: ${theme.colors.primary.base};
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 0;
      transition: all 0.1s ease;
    }

    .search-tag-remove:hover {
      background: ${theme.colors.primary.light3};
      color: ${theme.colors.primary.dark1};
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

    .search-result-item.active {
      background: ${theme.colors.primary.light5};
      outline: 1px solid ${theme.colors.primary.light2};
    }

    .search-result-name {
      font-weight: ${theme.typography.weights.medium};
      color: ${theme.colors.grayscale.dark2};
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

    .selected-items-tags {
      display: flex;
      flex-wrap: wrap;
      gap: ${theme.gridUnit}px;
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
  const { height, width, processedData, topSize } = props;

  const [selectedJourney, setSelectedJourney] =
    useState<ProcessedJourney | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
  const [activeSearchIndex, setActiveSearchIndex] = useState(0);

  // Filter journeys using AND condition
  const filteredJourneys = useMemo(() => {
    const journeys = processedData?.journeys || [];

    if (selectedNodes.length === 0) {
      return journeys;
    }

    // AND logic: journey must contain ALL selected nodes
    return journeys.filter(journey => {
      const journeyNodesLower = journey.nodesList.map(n => n.toLowerCase());
      return selectedNodes.every(selectedNode =>
        journeyNodesLower.some(n => n === selectedNode.toLowerCase()),
      );
    });
  }, [processedData, selectedNodes]);

  const availableNodes = useMemo(() => {
    const nodes = new Set<string>();

    filteredJourneys.forEach(journey => {
      journey.nodesList.forEach(node => {
        if (!selectedNodes.includes(node)) {
          nodes.add(node);
        }
      });
    });

    return Array.from(nodes).sort();
  }, [filteredJourneys, selectedNodes]);

  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return [];

    return availableNodes
      .filter(node => matchesSearchQuery(node, searchQuery))
      .slice(0, 10);
  }, [availableNodes, searchQuery]);

  const topJourneys = useMemo(() => {
    return filteredJourneys.slice(0, topSize);
  }, [filteredJourneys, topSize]);

  useEffect(() => {
    setSelectedJourney(null);
    setSearchQuery('');
    setSelectedNodes([]);
    setActiveSearchIndex(0);
  }, [processedData]);

  useEffect(() => {
    if (!selectedJourney) return undefined;

    const handleEscClose = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelectedJourney(null);
      }
    };

    window.addEventListener('keydown', handleEscClose);
    return () => window.removeEventListener('keydown', handleEscClose);
  }, [selectedJourney]);

  useEffect(() => {
    if (!searchQuery.trim() || searchResults.length === 0) {
      setActiveSearchIndex(0);
      return;
    }
    setActiveSearchIndex(prev => Math.min(prev, searchResults.length - 1));
  }, [searchQuery, searchResults]);

  const handleSearchResultClick = (node: string) => {
    setSelectedNodes(prev => (prev.includes(node) ? prev : [...prev, node]));
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
      const candidate = searchResults[activeSearchIndex];
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

  const handleRemoveSelectedNode = (node: string) => {
    setSelectedNodes(prev => prev.filter(n => n !== node));
  };

  const handleClearAllSelected = () => {
    setSelectedNodes([]);
  };

  const handleJourneyClick = (journey: ProcessedJourney) => {
    setSelectedJourney(journey);
  };

  const handleCloseModal = () => {
    setSelectedJourney(null);
  };

  const getRankClass = (rank: number) => {
    if (rank === 1) return 'rank-badge rank-1';
    if (rank === 2) return 'rank-badge rank-2';
    if (rank === 3) return 'rank-badge rank-3';
    return 'rank-badge rank-default';
  };

  return (
    <Styles style={{ height, width }}>
      {/* Search Section */}
      <div className="search-section">
        <div className="search-container">
          <div
            className="search-select-box"
            onClick={() => setIsSearchFocused(true)}
          >
            {selectedNodes.map(node => (
              <span key={node} className="search-tag">
                {node}
                <button
                  className="search-tag-remove"
                  onClick={e => {
                    e.stopPropagation();
                    handleRemoveSelectedNode(node);
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
                selectedNodes.length === 0
                  ? 'Search and select locations to filter...'
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
                    key={node}
                    className={`search-result-item ${activeSearchIndex === index ? 'active' : ''
                      }`}
                    onClick={() => handleSearchResultClick(node)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleSearchResultClick(node);
                    }}
                  >
                    <span className="search-result-name">{node}</span>
                  </div>
                ))
              ) : (
                <div className="search-no-results">
                  No locations found for "{searchQuery}"
                </div>
              )}
            </div>
          )}
          <div className="search-hint">
            Filter journeys containing ALL selected locations
          </div>
        </div>

        {/* Selected Items Panel */}
        {selectedNodes.length > 0 && (
          <div className="selected-items-panel">
            <div className="selected-items-header">
              <span className="selected-items-title">
                Filter by ({selectedNodes.length})
              </span>
              <button
                className="clear-all-btn"
                onClick={handleClearAllSelected}
              >
                Clear All
              </button>
            </div>
            <div className="selected-items-tags">
              {selectedNodes.map(node => (
                <span key={node} className="search-tag">
                  {node}
                  <button
                    className="search-tag-remove"
                    onClick={() => handleRemoveSelectedNode(node)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th className="rank-cell">Rank</th>
              <th className="footfall-cell">Total Footfall</th>
              <th>Journey Path</th>
            </tr>
          </thead>
          <tbody>
            {topJourneys.length === 0 ? (
              <tr>
                <td colSpan={3} className="empty-state">
                  No journeys match the current filter.
                </td>
              </tr>
            ) : (
              topJourneys.map((journey, index) => (
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
                          {nodeIndex > 0 && (
                            <span className="node-arrow">→</span>
                          )}
                          <span className="node-chip">
                            {node}
                          </span>
                        </React.Fragment>
                      ))}
                      {journey.nodesList.length > 3 && (
                        <span className="more-nodes">
                          +{journey.nodesList.length - 3}
                        </span>
                      )}
                      <span className="click-hint">
                        Click to view full path
                      </span>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Journey Details Modal - Full Path Only */}
      {selectedJourney && (
        <div className="modal-overlay" onClick={handleCloseModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <button className="close-btn" onClick={handleCloseModal}>
              ×
            </button>
            <div className="modal-header">
              <h3 className="modal-title">Full Journey Path</h3>
            </div>
            <div className="modal-body">
              <div className="footfall-pill">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
                </svg>
                Total Footfall: {selectedJourney.totalFootfall.toLocaleString()}
              </div>
              <div className="journey-path">
                {selectedJourney.nodesList.map((node, index) => (
                  <React.Fragment key={`path-${node}-${index}`}>
                    {index > 0 && <span className="path-connector">→</span>}
                    <div className="path-node">
                      <span className="path-node-number">{index + 1}</span>
                      <span>{node}</span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </Styles>
  );
}
