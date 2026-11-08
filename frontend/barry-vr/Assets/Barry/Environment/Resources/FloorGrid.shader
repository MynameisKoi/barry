Shader "Barry/Environment/FloorGrid"
{
    Properties
    {
        _FloorColor ("Floor", Color) = (0.051,0.075,0.22,1)
        _GridColor ("Grid", Color) = (0.369,0.882,1,0.28)
        _GridSpacing ("Grid Spacing", Float) = 0.5
        _FadeDistance ("Fade Distance", Float) = 14
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "RenderPipeline"="UniversalPipeline" }
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            struct Attributes { float4 positionOS : POSITION; };
            struct Varyings { float4 positionCS : SV_POSITION; float3 positionWS : TEXCOORD0; };
            CBUFFER_START(UnityPerMaterial)
                half4 _FloorColor, _GridColor;
                float _GridSpacing, _FadeDistance;
            CBUFFER_END

            Varyings Vert(Attributes input)
            {
                Varyings output;
                output.positionWS = TransformObjectToWorld(input.positionOS.xyz);
                output.positionCS = TransformWorldToHClip(output.positionWS);
                return output;
            }

            half4 Frag(Varyings input) : SV_Target
            {
                float2 coordinate = input.positionWS.xz / max(_GridSpacing, 0.001);
                float2 distanceToLine = abs(frac(coordinate - 0.5) - 0.5) / max(fwidth(coordinate), 0.0001);
                float gridLine = 1.0 - saturate(min(distanceToLine.x, distanceToLine.y));
                float distanceFade = 1.0 - smoothstep(_FadeDistance * 0.35, _FadeDistance, distance(input.positionWS.xz, _WorldSpaceCameraPos.xz));
                half blend = gridLine * distanceFade * _GridColor.a;
                return half4(lerp(_FloorColor.rgb, _GridColor.rgb, blend), 1.0);
            }
            ENDHLSL
        }
    }
}
