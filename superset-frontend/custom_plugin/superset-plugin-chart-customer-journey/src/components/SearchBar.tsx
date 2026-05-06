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
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { css, styled } from '@superset-ui/core';
import { matchesSearchQuery } from '../utils/search';

const Styles = styled.div`
  ${({ theme }) => css`
    background: ${theme.colors.grayscale.light5};
    border-bottom: 1px solid ${theme.colors.grayscale.light2};
    padding: ${theme.gridUnit * 3}px;

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

    .search-clear-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      align-self: center;
      border: none;
      background: transparent;
      color: ${theme.colors.primary.base};
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.medium};
      cursor: pointer;
      padding: ${theme.gridUnit}px;
      white-space: nowrap;
    }

    .search-clear-btn:hover {
      color: ${theme.colors.primary.dark1};
      text-decoration: underline;
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

    .search-result-item:hover,
    .search-result-item.active {
      background: ${theme.colors.primary.light5};
    }

    .search-result-item.active {
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
  `}
`;

interface SearchBarProps {
  availableNodes: string[];
  selectedNodes: string[];
  onNodesChange: (nodes: string[]) => void;
}

const MAX_SEARCH_RESULTS = 10;

export default function SearchBar({
  availableNodes,
  selectedNodes,
  onNodesChange,
}: SearchBarProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [activeSearchIndex, setActiveSearchIndex] = useState(0);

  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return [];
    return availableNodes
      .filter(node => matchesSearchQuery(node, searchQuery))
      .slice(0, MAX_SEARCH_RESULTS);
  }, [availableNodes, searchQuery]);

  // Clamp the active index when results shrink
  useEffect(() => {
    if (searchResults.length > 0) {
      setActiveSearchIndex(prev => Math.min(prev, searchResults.length - 1));
    } else {
      setActiveSearchIndex(0);
    }
  }, [searchResults.length]);

  const addNode = useCallback(
    (node: string) => {
      const nodeLower = node.toLowerCase().trim();
      const alreadySelected = selectedNodes.some(
        n => n.toLowerCase().trim() === nodeLower,
      );
      if (!alreadySelected) {
        onNodesChange([...selectedNodes, node]);
      }
      setSearchQuery('');
      setIsSearchFocused(false);
      setActiveSearchIndex(0);
    },
    [selectedNodes, onNodesChange],
  );

  const removeNode = useCallback(
    (node: string) => {
      onNodesChange(selectedNodes.filter(n => n !== node));
    },
    [selectedNodes, onNodesChange],
  );

  const clearAll = useCallback(() => {
    onNodesChange([]);
  }, [onNodesChange]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (
        (event.key === 'Backspace' || event.key === 'Delete') &&
        !searchQuery &&
        selectedNodes.length > 0
      ) {
        event.preventDefault();
        onNodesChange(selectedNodes.slice(0, -1));
        return;
      }

      if (event.key === 'Escape') {
        setIsSearchFocused(false);
        return;
      }

      if (!searchQuery.trim() || searchResults.length === 0) return;

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
        if (candidate) addNode(candidate);
      }
    },
    [
      searchQuery,
      searchResults,
      activeSearchIndex,
      selectedNodes,
      onNodesChange,
      addNode,
    ],
  );

  const showDropdown = isSearchFocused && searchQuery.trim().length > 0;

  return (
    <Styles>
      <div className="search-container">
        <div
          className="search-select-box"
          onClick={() => setIsSearchFocused(true)}
        >
          {selectedNodes.map(node => (
            <span key={node} className="search-tag">
              {node}
              <button
                type="button"
                className="search-tag-remove"
                onClick={e => {
                  e.stopPropagation();
                  removeNode(node);
                }}
                aria-label={`Remove ${node}`}
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
            onKeyDown={handleKeyDown}
            onFocus={() => setIsSearchFocused(true)}
            onBlur={() => setIsSearchFocused(false)}
          />
          {selectedNodes.length > 0 && (
            <button
              type="button"
              className="search-clear-btn"
              onClick={e => {
                e.stopPropagation();
                clearAll();
              }}
            >
              Clear
            </button>
          )}
        </div>

        {showDropdown && (
          <div className="search-results">
            {searchResults.length > 0 ? (
              searchResults.map((node, index) => (
                <div
                  key={node}
                  className={`search-result-item${activeSearchIndex === index ? ' active' : ''}`}
                  // Prevent blur from firing before click
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => addNode(node)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') addNode(node);
                  }}
                >
                  <span className="search-result-name">{node}</span>
                </div>
              ))
            ) : (
              <div className="search-no-results">
                No locations found for &ldquo;{searchQuery}&rdquo;
              </div>
            )}
          </div>
        )}

        <div className="search-hint">
          Filter journeys containing ALL selected locations
        </div>
      </div>
    </Styles>
  );
}
