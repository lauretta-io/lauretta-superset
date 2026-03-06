/**
 * Custom control that fetches floor choices from /api/v1/lauretta/floors
 * (sourced from config.json) and updates the hidden floor_image control
 * whenever the user picks a different floor.
 */
import React, { useEffect, useState, useCallback } from 'react';
import { SupersetClient, t } from '@superset-ui/core';

interface Floor {
  name: string;
  image: string;
}

export interface FloorSelectControlProps {
  value: string;
  onChange: (value: string) => void;
  name: string;
  label?: string;
  description?: string;
  actions?: {
    setControlValue?: (controlName: string, value: any) => void;
  };
}

export default function FloorSelectControl(props: FloorSelectControlProps) {
  const { value, onChange, actions } = props;
  const labelText = props.label || t('Floor');
  const [floors, setFloors] = useState<Floor[]>([]);
  const [loading, setLoading] = useState(true);

  const setFloorImage = useCallback(
    (floorName: string, list: Floor[]) => {
      const floor = list.find(f => f.name === floorName);
      if (floor && actions?.setControlValue) {
        actions.setControlValue('floor_image', floor.image);
      }
    },
    [actions],
  );

  useEffect(() => {
    SupersetClient.get({ endpoint: '/api/v1/lauretta/floors' })
      .then(({ json }) => {
        const data = (json as Floor[]) || [];
        setFloors(data);
        setLoading(false);

        // If current value matches a floor, sync its image
        if (value && data.length > 0) {
          setFloorImage(value, data);
        }
        // If no value yet and floors exist, select the first one
        if (!value && data.length > 0) {
          onChange(data[0].name);
          setFloorImage(data[0].name, data);
        }
      })
      .catch(() => {
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const selected = e.target.value;
    onChange(selected);
    setFloorImage(selected, floors);
  };

  const selectStyle: React.CSSProperties = {
    width: '100%',
    padding: '6px 12px',
    borderRadius: 4,
    border: '1px solid #ccc',
    fontSize: 14,
    backgroundColor: '#fff',
  };

  if (loading) {
    return (
      <select disabled style={selectStyle}>
        <option>Loading floors…</option>
      </select>
    );
  }

  return (
    <div style={{ width: '100%' }}>
      <label
        style={{
          display: 'block',
          fontSize: 12,
          fontWeight: 500,
          marginBottom: 4,
          color: '#333',
        }}
      >
        {labelText}
      </label>
      {floors.length === 0 ? (
        <select disabled style={selectStyle}>
          <option>No floors available</option>
        </select>
      ) : (
        <select value={value || ''} onChange={handleChange} style={selectStyle}>
          {floors.map(floor => (
            <option key={floor.name} value={floor.name}>
              {floor.name}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
