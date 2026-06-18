using ModelContextProtocol.Server;
using SolidWorksBridge.SolidWorks;
using System.ComponentModel;
using System.Text.Json;
using System.Threading.Tasks;

namespace SolidWorksMcpApp.Tools;

[McpServerToolType]
public class FeatureDimensionTools(StaDispatcher sta, IFeatureDimensionService featureDimensions, IFeatureCacheManager cacheManager)
{
    [McpServerTool, Description("Directly sets a dimension value using its exact token (e.g., 'D7@Edge Line - Flange 1'). Units default to mm. Automatically invalidates cache after change.")]
    public async Task<string> SetFeatureDimensionValue(
        [Description("The name of the feature to update")] string featureName,
        [Description("The exact dimension token")] string dimensionToken,
        [Description("The numerical value to apply")] double value,
        [Description("Unit of measurement (mm, cm, in, deg). Defaults to mm.")] string unit = "mm")
    {
        var payload = new { featureName, dimensionToken, value, unit };
        var result = await sta.InvokeLoggedAsync(nameof(SetFeatureDimensionValue), payload, 
            () =>
            {
                var modificationResult = featureDimensions.SetDimensionValue(featureName, dimensionToken, value, unit);
                // Invalidate cache after successful modification
                cacheManager.InvalidateActiveScope(invalidateParents: true);
                return modificationResult;
            });
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Inspects a feature to return all its editable parameters and dimensions with their current values and types (length, angle, radius, etc.).")]
    public async Task<string> GetFeatureParameters(
        [Description("The name of the feature to inspect")] string featureName)
    {
        var payload = new { featureName };
        var result = await sta.InvokeLoggedAsync(nameof(GetFeatureParameters), payload, 
            () => featureDimensions.GetFeatureParameters(featureName));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Intelligently finds a specific dimension token (e.g., 'D7@Edge Line...') based on its physical type ('length', 'angle', 'radius', 'diameter') rather than guessing the string.")]
    public async Task<string> IdentifyDimensionByType(
        [Description("The name of the feature")] string featureName,
        [Description("The type of dimension to find: 'length', 'angle', 'radius', or 'diameter'")] string dimensionType)
    {
        var payload = new { featureName, dimensionType };
        var result = await sta.InvokeLoggedAsync(nameof(IdentifyDimensionByType), payload, 
            () => featureDimensions.IdentifyDimensionByType(featureName, dimensionType));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Edits a dimension inside a child part or subassembly directly from the root assembly context. Automatically invalidates cache after change.")]
    public async Task<string> EditAssemblyChildDimension(
        [Description("The instance name of the child component in the assembly")] string componentName,
        [Description("The name of the feature inside the child component")] string featureName,
        [Description("The dimension token to change")] string dimensionToken,
        [Description("The numerical value to apply")] double value)
    {
        var payload = new { componentName, featureName, dimensionToken, value };
        var result = await sta.InvokeLoggedAsync(nameof(EditAssemblyChildDimension), payload, 
            () =>
            {
                var modificationResult = featureDimensions.EditAssemblyChildDimension(componentName, featureName, dimensionToken, value);
                // Invalidate cache for the modified component and parents
                cacheManager.InvalidateScope(componentName);
                cacheManager.InvalidateActiveScope(invalidateParents: true);
                return modificationResult;
            });
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Add a driven reference radius or diameter dimension by selecting a cylindrical face from ListEntities.")]
    public async Task<string> AddReferenceCircularDimension(
        [Description("Dimension kind: Radius or Diameter.")] string dimensionKind,
        [Description("Entity type from ListEntities. Currently only Face is supported.")] string entityType,
        [Description("Zero-based entity index from ListEntities.")] int entityIndex,
        [Description("Optional component name for assembly context. Leave null for part context or top-level.")] string? componentName = null,
        [Description("Dimension placement X coordinate in meters.")] double x = 0.02,
        [Description("Dimension placement Y coordinate in meters.")] double y = 0.02,
        [Description("Dimension placement Z coordinate in meters.")] double z = 0)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(AddReferenceCircularDimension),
            new { dimensionKind, entityType, entityIndex, componentName, x, y, z },
            () =>
            {
                var kind = ToolArgumentParsing.ParseCircularReferenceDimensionKind(dimensionKind, nameof(dimensionKind));
                var type = ToolArgumentParsing.ParseSelectableEntityType(entityType, nameof(entityType));
                return featureDimensions.AddReferenceCircularDimension(kind, type, entityIndex, componentName, x, y, z);
            });
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Add a SolidWorks dimension to selected topology entities from ListEntities.")]
    public async Task<string> AddDimension(
        [Description("Dimension kind: Smart, Distance, Horizontal, Vertical, Radius, Diameter, or Angle.")] string dimensionKind,
        [Description("Dimension role: Reference or Driving.")] string dimensionRole,
        [Description("First entity type from ListEntities: Face, Edge, or Vertex.")] string firstEntityType,
        [Description("Zero-based first entity index from ListEntities.")] int firstEntityIndex,
        [Description("Optional second entity type from ListEntities.")] string? secondEntityType = null,
        [Description("Optional zero-based second entity index from ListEntities.")] int? secondEntityIndex = null,
        [Description("Optional component name for the first entity in assembly context.")] string? firstComponentName = null,
        [Description("Optional component name for the second entity in assembly context.")] string? secondComponentName = null,
        [Description("Dimension placement X coordinate in meters.")] double x = 0.02,
        [Description("Dimension placement Y coordinate in meters.")] double y = 0.02,
        [Description("Dimension placement Z coordinate in meters.")] double z = 0)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(AddDimension),
            new
            {
                dimensionKind, dimensionRole, firstEntityType, firstEntityIndex,
                secondEntityType, secondEntityIndex, firstComponentName, secondComponentName, x, y, z
            },
            () =>
            {
                var kind = ToolArgumentParsing.ParseAddDimensionKind(dimensionKind, nameof(dimensionKind));
                var role = ToolArgumentParsing.ParseAddDimensionRole(dimensionRole, nameof(dimensionRole));
                var firstType = ToolArgumentParsing.ParseSelectableEntityType(firstEntityType, nameof(firstEntityType));
                SelectableEntityType? secondType = string.IsNullOrWhiteSpace(secondEntityType)
                    ? null
                    : ToolArgumentParsing.ParseSelectableEntityType(secondEntityType, nameof(secondEntityType));

                return featureDimensions.AddDimension(
                    kind, role, firstType, firstEntityIndex, secondType, secondEntityIndex,
                    firstComponentName, secondComponentName, x, y, z);
            });
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("List bindable dimensions that belong to a named SolidWorks feature.")]
    public async Task<string> ListFeatureDimensions(
        [Description("Exact SolidWorks feature name, for example Boss-Extrude1 or Sketch2.")] string featureName)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(ListFeatureDimensions),
            new { featureName },
            () => featureDimensions.ListFeatureDimensions(featureName));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Create or update a global variable and bind the best-matching dimension of the specified feature based on a natural-language dimension description.")]
    public async Task<string> UpsertGlobalVariableAndBindFeatureDimensionByDescription(
        [Description("Exact SolidWorks feature name, for example Boss-Extrude1 or Sketch2.")] string featureName,
        [Description("Global variable name without surrounding quotes.")] string variableName,
        [Description("Right-hand-side expression, for example 100mm or 0.1.")] string expression,
        [Description("Natural-language description of the intended dimension, for example radius, diameter, height, width, or length.")] string dimensionDescription,
        [Description("When true, evaluates the equation immediately.")] bool solve = true)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(UpsertGlobalVariableAndBindFeatureDimensionByDescription),
            new { featureName, variableName, expression, dimensionDescription, solve },
            () => featureDimensions.UpsertGlobalVariableAndBindFeatureDimensionByDescription(
                featureName, variableName, expression, dimensionDescription, solve));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Ensure the specified feature has a bindable driving dimension, creating a radial or diameter dimension from its owning sketch when needed.")]
    public async Task<string> EnsureFeatureDimensionAndBindGlobalVariable(
        [Description("Exact SolidWorks feature name, for example Boss-Extrude1 or Sketch2.")] string featureName,
        [Description("Global variable name without surrounding quotes.")] string variableName,
        [Description("Right-hand-side expression, for example 100mm or 0.1.")] string expression,
        [Description("Natural-language description of the intended dimension, currently best for radius, diameter, length, width, or height.")] string dimensionDescription,
        [Description("When true, evaluates the equation immediately.")] bool solve = true)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(EnsureFeatureDimensionAndBindGlobalVariable),
            new { featureName, variableName, expression, dimensionDescription, solve },
            () => featureDimensions.EnsureFeatureDimensionAndBindGlobalVariable(
                featureName, variableName, expression, dimensionDescription, solve));
        return JsonSerializer.Serialize(result);
    }
}