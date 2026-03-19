import React, { useState } from 'react';
import { SupersetClient, t } from '@superset-ui/core';

export interface FloorSelectControlProps {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}

export default function FloorSelectControl(props: FloorSelectControlProps) {
  const { value, onChange } = props;
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  const currentUrl = (value || '').trim();

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';

    if (!file) {
      return;
    }

    setUploading(true);
    setError('');

    try {
      const body = new FormData();
      body.append('file', file);

      const response = await SupersetClient.post({
        endpoint: '/api/v1/lauretta/images/upload',
        body,
        headers: { Accept: 'application/json' },
      });

      const publicUrl =
        (response.json?.public_url as string | undefined) ||
        (response.json?.result?.public_url as string | undefined) ||
        '';

      if (!publicUrl) {
        throw new Error('Upload did not return a public URL');
      }

      onChange(publicUrl);
    } catch (uploadError) {
      setError(t('Image upload failed. Please try again.'));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ width: '100%' }}>
      <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 6 }}>
        {props.label || t('Map image')}
      </div>

      <input
        type="file"
        accept=".png,.jpg,.jpeg,.gif,.webp"
        onChange={handleUpload}
        disabled={uploading}
        style={{ width: '100%' }}
      />

      {currentUrl ? (
        <div style={{ marginTop: 6 }}>
          <a href={currentUrl} target="_blank" rel="noreferrer">
            {t('View current map image')}
          </a>
        </div>
      ) : null}

      {uploading ? (
        <div style={{ marginTop: 6 }}>{t('Uploading image...')}</div>
      ) : null}

      {error ? (
        <div style={{ marginTop: 6, color: '#d14343', fontSize: 12 }}>
          {error}
        </div>
      ) : null}
    </div>
  );
}
