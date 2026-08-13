'use client';

import React, { useState } from 'react';

interface RealPoiImageProps {
  photoUrl?: string;
  poiName: string;
  className?: string;
}

export default function RealPoiImage({ photoUrl, poiName, className = '' }: RealPoiImageProps) {
  const [imgError, setImgError] = useState(false);

  // 判断是否为有效的 HTTP 图片 URL
  const isValidUrl = photoUrl && (photoUrl.startsWith('http://') || photoUrl.startsWith('https://'));

  // 若不是有效 URL 或加载失败，根据景点名称通过在线关键字源获取高清真实匹配图片
  const getDynamicKeywordUrl = (name: string) => {
    const encodedName = encodeURIComponent(name);
    return `https://source.unsplash.com/featured/800x600/?${encodedName},travel,landmark`;
  };

  // 备用稳定风景/美食高真源
  const getCategoryFallback = (name: string) => {
    if (name.includes('餐') || name.includes('店') || name.includes('美食') || name.includes('馆') || name.includes('小吃')) {
      return 'https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=800&q=80';
    }
    if (name.includes('谷') || name.includes('山') || name.includes('公园') || name.includes('河')) {
      return 'https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?auto=format&fit=crop&w=800&q=80';
    }
    return 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=800&q=80';
  };

  const finalSrc = isValidUrl && !imgError 
    ? photoUrl 
    : (imgError ? getCategoryFallback(poiName) : getDynamicKeywordUrl(poiName));

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 ${className}`}>
      <img
        src={finalSrc}
        alt={poiName}
        onError={() => setImgError(true)}
        className="w-full h-full object-cover transition-transform duration-500 hover:scale-105"
        loading="lazy"
      />
    </div>
  );
}