using SolidWorksBridge.SolidWorks;
using Moq;
using System.Text;
using System.Text.Json;

namespace SolidWorksBridge.Tests.SolidWorks;

public class AssemblyEntityAnnotationServiceTests
{
    [Theory]
    [InlineData("拉伸1", null)]
    [InlineData("拉伸1", "")]
    [InlineData("草图1", "ProfileFeature")]
    [InlineData("折弯1", "OneBend")]
    public void ShouldCaptureFeatureTarget_FiltersNonDimensionFeatureTypes(string? featureName, string? featureTypeName)
    {
        Assert.False(AssemblyEntityAnnotationService.ShouldCaptureFeatureTarget(featureName, featureTypeName));
    }

    [Theory]
    [InlineData("3D草图1", "3DProfileFeature")]
    [InlineData("拉伸1", "Extrude")]
    [InlineData("基体法兰1", "BaseFlange")]
    public void ShouldCaptureFeatureTarget_KeepsPhysicalOrEditableFeatureTypes(string featureName, string featureTypeName)
    {
        Assert.True(AssemblyEntityAnnotationService.ShouldCaptureFeatureTarget(featureName, featureTypeName));
    }

    [Fact]
    public void ShouldKeepCaptureTarget_WhenFeatureTypeIsRequired_FiltersTargetsWithoutValidFeatureTypeName()
    {
        var component = new AssemblyEntityCaptureTargetInfo
        {
            TargetId = "component-1",
            EntityKind = "component",
            DisplayName = "装配体/零件1",
        };
        var profile = new AssemblyEntityCaptureTargetInfo
        {
            TargetId = "feature-profile",
            EntityKind = "feature",
            DisplayName = "装配体/草图1",
            FeatureName = "草图1",
            FeatureTypeName = "ProfileFeature",
        };
        var extrude = new AssemblyEntityCaptureTargetInfo
        {
            TargetId = "feature-extrude",
            EntityKind = "feature",
            DisplayName = "装配体/拉伸1",
            FeatureName = "拉伸1",
            FeatureTypeName = "Extrude",
        };

        Assert.False(AssemblyEntityAnnotationService.ShouldKeepCaptureTarget(component, requireFeatureTypeName: true));
        Assert.False(AssemblyEntityAnnotationService.ShouldKeepCaptureTarget(profile, requireFeatureTypeName: true));
        Assert.True(AssemblyEntityAnnotationService.ShouldKeepCaptureTarget(extrude, requireFeatureTypeName: true));
    }

    [Fact]
    public void ShouldKeepCaptureTarget_WhenFeatureTypeIsNotRequired_KeepsNonFeatureTargets()
    {
        var component = new AssemblyEntityCaptureTargetInfo
        {
            TargetId = "component-1",
            EntityKind = "component",
            DisplayName = "装配体/零件1",
        };

        Assert.True(AssemblyEntityAnnotationService.ShouldKeepCaptureTarget(component, requireFeatureTypeName: false));
    }

    [Fact]
    public void QueryAssemblyStructuralComponentTargets_FindsStructualInfoByType()
    {
        string directory = Path.Combine(Path.GetTempPath(), $"sw-mcp-structural-{Guid.NewGuid():N}");
        Directory.CreateDirectory(directory);
        string manifestPath = Path.Combine(directory, "manifest.json");
        try
        {
            var manifest = new
            {
                schemaVersion = AssemblyEntityAnnotationService.CaptureSchemaVersion,
                manifestPath,
                outputDirectory = directory,
                targets = new object[]
                {
                    new
                    {
                        targetId = "height-target",
                        entityKind = "feature",
                        displayName = "active assembly/0:HeightFeature",
                        featureName = "HeightFeature",
                        featureTypeName = "Extrude",
                        featurePath = "0:HeightFeature",
                        hasSubFeatures = true,
                        StructualInfo = new
                        {
                            IsStructualComponent = true,
                            Type = "Height",
                        },
                    },
                    new
                    {
                        targetId = "length-target",
                        entityKind = "feature",
                        displayName = "active assembly/1:LengthFeature",
                        featureName = "LengthFeature",
                        featureTypeName = "Extrude",
                        featurePath = "1:LengthFeature",
                        hasSubFeatures = true,
                        StructualInfo = new
                        {
                            IsStructualComponent = true,
                            Type = "Length",
                        },
                    },
                    new
                    {
                        targetId = "normal-target",
                        entityKind = "feature",
                        displayName = "active assembly/2:InternalFeature",
                        featureName = "InternalFeature",
                        featureTypeName = "Extrude",
                        featurePath = "2:InternalFeature",
                    },
                },
            };
            File.WriteAllText(
                manifestPath,
                JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }),
                Encoding.UTF8);

            var service = new AssemblyEntityAnnotationService(new Mock<ISwConnectionManager>().Object);

            var result = service.QueryAssemblyStructuralComponentTargets(
                manifestPath,
                type: null,
                query: "我想将这整个零件的高度增高100mm",
                maxResults: 10);

            Assert.Equal("height", result.RequestedType);
            var match = Assert.Single(result.Matches);
            Assert.Equal("height-target", match.Target.TargetId);
            Assert.Equal("height", match.StructuralType);
            Assert.True(match.StructualInfo.IsMarkedStructural);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public void SearchStructuralFeatureTargets_FindsStructuralFeatureByGlobalAxisHint()
    {
        string directory = Path.Combine(Path.GetTempPath(), $"sw-mcp-feature-structure-{Guid.NewGuid():N}");
        Directory.CreateDirectory(directory);
        string annotationPath = Path.Combine(directory, "feature-structure-annotations.json");
        try
        {
            var annotationSet = new
            {
                schemaVersion = AssemblyEntityAnnotationService.FeatureStructureAnnotationSchemaVersion,
                annotationPath,
                targetCount = 3,
                completedCount = 3,
                failedCount = 0,
                entries = new object[]
                {
                    new
                    {
                        annotationStatus = "completed",
                        sourceIndex = 0,
                        nodeId = "node-width",
                        name = "WidthFeature",
                        featureTypeName = "Extrusion",
                        graphPath = "root/WidthFeature",
                        isStructural = true,
                        structuralCategory = "base_plate",
                        primaryDirection = new
                        {
                            label = "front.horizontal",
                            view = "front",
                            axis = "horizontal",
                            global_axis_hint = "X_width",
                            influence = "sets_overall_extent",
                            confidence = 0.9,
                        },
                        affectedDirections = new object[]
                        {
                            new
                            {
                                label = "front.horizontal",
                                view = "front",
                                axis = "horizontal",
                                global_axis_hint = "X_width",
                                influence = "sets_overall_extent",
                                confidence = 0.9,
                            },
                        },
                        dimensionChangeIntent = new
                        {
                            can_drive_overall_size_change = true,
                            recommended_edit_axis = "width",
                            edit_relevance = "direct",
                        },
                        confidence = 0.92,
                    },
                    new
                    {
                        annotationStatus = "completed",
                        sourceIndex = 1,
                        nodeId = "node-height",
                        name = "HeightPost",
                        featureTypeName = "WeldMemberFeat",
                        graphPath = "root/HeightPost",
                        isStructural = true,
                        structuralCategory = "frame_member",
                        primaryDirection = new
                        {
                            label = "front.vertical",
                            view = "front",
                            axis = "vertical",
                            global_axis_hint = "Z_height",
                            influence = "sets_overall_extent",
                            confidence = 0.95,
                        },
                        affectedDirections = new object[]
                        {
                            new
                            {
                                label = "right.vertical",
                                view = "right",
                                axis = "vertical",
                                global_axis_hint = "Z_height",
                                influence = "sets_overall_extent",
                                confidence = 0.88,
                            },
                        },
                        dimensionChangeIntent = new
                        {
                            can_drive_overall_size_change = true,
                            recommended_edit_axis = "height",
                            edit_relevance = "direct",
                        },
                        confidence = 0.96,
                    },
                    new
                    {
                        annotationStatus = "completed",
                        sourceIndex = 2,
                        nodeId = "node-decorative",
                        name = "DecorativeCover",
                        featureTypeName = "Extrusion",
                        isStructural = false,
                        primaryDirection = new
                        {
                            label = "front.vertical",
                            view = "front",
                            axis = "vertical",
                            global_axis_hint = "Z_height",
                            influence = "local_only",
                            confidence = 0.4,
                        },
                        dimensionChangeIntent = new
                        {
                            can_drive_overall_size_change = false,
                            recommended_edit_axis = "height",
                            edit_relevance = "none",
                        },
                        confidence = 0.4,
                    },
                },
            };

            File.WriteAllText(
                annotationPath,
                JsonSerializer.Serialize(annotationSet, new JsonSerializerOptions { WriteIndented = true }),
                Encoding.UTF8);

            var service = new AssemblyEntityAnnotationService(new Mock<ISwConnectionManager>().Object);

            var result = service.SearchStructuralFeatureTargets(
                annotationPath,
                direction: null,
                query: "整体高度增加100mm",
                onlyStructural: true,
                maxResults: 10);

            Assert.Equal("Z_height", result.RequestedGlobalAxisHint);
            var match = Assert.Single(result.Matches);
            Assert.Equal("node-height", match.Annotation.NodeId);
            Assert.Equal("Z_height", Assert.Single(match.MatchedGlobalAxisHints));
            Assert.True(match.PrimaryDirectionMatched);
            Assert.True(match.Annotation.IsStructural);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }
}
