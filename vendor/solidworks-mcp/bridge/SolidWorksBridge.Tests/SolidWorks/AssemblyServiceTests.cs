using Moq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorksBridge.SolidWorks;

namespace SolidWorksBridge.Tests.SolidWorks;

public class AssemblyServiceTests
{
    [Fact]
    public void TraverseAssemblyFeatureTrees_PropagatesComponentContextToNestedFeatureNodes()
    {
        var feature = new Mock<Feature>();
        feature.Setup(f => f.Name).Returns("Boss-Extrude1");
        feature.Setup(f => f.GetTypeName2()).Returns("BossExtrude");

        var component = new Mock<Component2>();
        component.Setup(c => c.Name2).Returns("SquareTube-1");
        component.Setup(c => c.GetPathName()).Returns(@"C:\Models\SquareTube.sldprt");

        var featureNode = new Mock<TreeControlItem>();
        featureNode.SetupGet(n => n.Text).Returns("Boss-Extrude1");
        featureNode.SetupGet(n => n.ObjectType).Returns(101);
        featureNode.SetupGet(n => n.Object).Returns(feature.Object);
        featureNode.SetupGet(n => n.IsRoot).Returns(false);
        featureNode.SetupGet(n => n.Expanded).Returns(false);
        featureNode.Setup(n => n.GetFirstChild()).Returns((TreeControlItem)null!);
        featureNode.Setup(n => n.GetNext()).Returns((TreeControlItem)null!);

        var componentNode = new Mock<TreeControlItem>();
        componentNode.SetupGet(n => n.Text).Returns("SquareTube-1");
        componentNode.SetupGet(n => n.ObjectType).Returns(202);
        componentNode.SetupGet(n => n.Object).Returns(component.Object);
        componentNode.SetupGet(n => n.IsRoot).Returns(false);
        componentNode.SetupGet(n => n.Expanded).Returns(true);
        componentNode.Setup(n => n.GetFirstChild()).Returns(featureNode.Object);
        componentNode.Setup(n => n.GetNext()).Returns((TreeControlItem)null!);

        var rootNode = new Mock<TreeControlItem>();
        rootNode.SetupGet(n => n.Text).Returns("TopAsm");
        rootNode.SetupGet(n => n.ObjectType).Returns(1);
        rootNode.SetupGet(n => n.Object).Returns((object?)null);
        rootNode.SetupGet(n => n.IsRoot).Returns(true);
        rootNode.SetupGet(n => n.Expanded).Returns(true);
        rootNode.Setup(n => n.GetFirstChild()).Returns(componentNode.Object);
        rootNode.Setup(n => n.GetNext()).Returns((TreeControlItem)null!);

        var featureManager = new Mock<IFeatureManager>();
        featureManager.Setup(fm => fm.GetFeatureTreeRootItem2(It.IsAny<int>())).Returns(rootNode.Object);

        var assembly = new Mock<IAssemblyDoc>();
        var model = assembly.As<IModelDoc2>();
        model.Setup(d => d.GetTitle()).Returns("TopAsm");
        model.Setup(d => d.GetPathName()).Returns(@"C:\Models\TopAsm.sldasm");
        model.Setup(d => d.GetType()).Returns((int)swDocumentTypes_e.swDocASSEMBLY);

        var swApp = new Mock<ISldWorksApp>();
        swApp.Setup(s => s.IActiveDoc2).Returns(model.Object);
        swApp.Setup(s => s.FeatureManager).Returns(featureManager.Object);

        var manager = new Mock<ISwConnectionManager>();
        manager.Setup(m => m.IsConnected).Returns(true);
        manager.Setup(m => m.SwApp).Returns(swApp.Object);
        manager.Setup(m => m.EnsureConnected());

        var result = new AssemblyService(manager.Object).TraverseAssemblyFeatureTrees();

        var nestedFeature = Assert.Single(result.Nodes.Where(node => node.FeatureName == "Boss-Extrude1"));
        Assert.Equal("SquareTube-1", nestedFeature.ComponentName);
        Assert.Equal(@"C:\Models\SquareTube.sldprt", nestedFeature.ComponentPath);
        Assert.Equal("SquareTube-1", nestedFeature.HierarchyPath);
    }

    [Fact]
    public void TraverseAssemblyFeatureTrees_ReadsLoadedChildDocumentFeaturesWhenUiNodeIsCollapsed()
    {
        var sketchFeature = new Mock<Feature>();
        sketchFeature.Setup(f => f.Name).Returns("草图1");
        sketchFeature.Setup(f => f.GetTypeName2()).Returns("ProfileFeature");
        sketchFeature.Setup(f => f.GetFirstSubFeature()).Returns((Feature)null!);
        sketchFeature.Setup(f => f.GetNextFeature()).Returns((Feature)null!);

        var partDocument = new Mock<IModelDoc2>();
        partDocument.Setup(d => d.GetTitle()).Returns("SquareTube.SLDPRT");
        partDocument.Setup(d => d.GetPathName()).Returns(@"C:\Models\SquareTube.sldprt");
        partDocument.Setup(d => d.FirstFeature()).Returns(sketchFeature.Object);

        var component = new Mock<Component2>();
        component.Setup(c => c.Name2).Returns("SquareTube-1");
        component.Setup(c => c.GetPathName()).Returns(@"C:\Models\SquareTube.sldprt");
        component.Setup(c => c.GetModelDoc2()).Returns(partDocument.Object);
        component.Setup(c => c.GetChildren()).Returns(Array.Empty<object>());

        var componentNode = new Mock<TreeControlItem>();
        componentNode.SetupGet(n => n.Text).Returns("SquareTube-1");
        componentNode.SetupGet(n => n.ObjectType).Returns(202);
        componentNode.SetupGet(n => n.Object).Returns(component.Object);
        componentNode.SetupGet(n => n.IsRoot).Returns(false);
        componentNode.SetupGet(n => n.Expanded).Returns(false);
        componentNode.Setup(n => n.GetFirstChild()).Returns((TreeControlItem)null!);
        componentNode.Setup(n => n.GetNext()).Returns((TreeControlItem)null!);

        var rootNode = new Mock<TreeControlItem>();
        rootNode.SetupGet(n => n.Text).Returns("TopAsm");
        rootNode.SetupGet(n => n.ObjectType).Returns(1);
        rootNode.SetupGet(n => n.Object).Returns((object?)null);
        rootNode.SetupGet(n => n.IsRoot).Returns(true);
        rootNode.SetupGet(n => n.Expanded).Returns(true);
        rootNode.Setup(n => n.GetFirstChild()).Returns(componentNode.Object);
        rootNode.Setup(n => n.GetNext()).Returns((TreeControlItem)null!);

        var featureManager = new Mock<IFeatureManager>();
        featureManager.Setup(fm => fm.GetFeatureTreeRootItem2(It.IsAny<int>())).Returns(rootNode.Object);

        var assembly = new Mock<IAssemblyDoc>();
        var model = assembly.As<IModelDoc2>();
        model.Setup(d => d.GetTitle()).Returns("TopAsm");
        model.Setup(d => d.GetPathName()).Returns(@"C:\Models\TopAsm.sldasm");
        model.Setup(d => d.GetType()).Returns((int)swDocumentTypes_e.swDocASSEMBLY);

        var swApp = new Mock<ISldWorksApp>();
        swApp.Setup(s => s.IActiveDoc2).Returns(model.Object);
        swApp.Setup(s => s.FeatureManager).Returns(featureManager.Object);

        var manager = new Mock<ISwConnectionManager>();
        manager.Setup(m => m.IsConnected).Returns(true);
        manager.Setup(m => m.SwApp).Returns(swApp.Object);
        manager.Setup(m => m.EnsureConnected());

        var result = new AssemblyService(manager.Object).TraverseAssemblyFeatureTrees();

        var loadedSketch = Assert.Single(result.Nodes.Where(node => node.FeatureName == "草图1"));
        Assert.True(loadedSketch.IsSketch);
        Assert.Equal("SquareTube-1", loadedSketch.ComponentName);
        Assert.Equal(@"C:\Models\SquareTube.sldprt", loadedSketch.ComponentPath);
        Assert.Equal("SquareTube-1", loadedSketch.HierarchyPath);
        Assert.Equal("SquareTube.SLDPRT", loadedSketch.DocumentTitle);
    }

    [Fact]
    public void OpenComponentForEditing_RequiresConfirmedSingleAssemblyMatch()
    {
        var assembly = new Mock<IAssemblyDoc>();
        var model = assembly.As<IModelDoc2>();
        model.Setup(d => d.GetPathName()).Returns(@"C:\Models\TopAsm.sldasm");

        var component = new Mock<Component2>();
        component.Setup(c => c.Name2).Returns("SquareTube-1");
        component.Setup(c => c.GetPathName()).Returns(@"C:\Models\SquareTube.sldprt");
        component.Setup(c => c.GetChildren()).Returns(Array.Empty<object>());
        assembly.Setup(a => a.GetComponents(true)).Returns(new object[] { component.Object });

        var swApp = new Mock<ISldWorksApp>();
        swApp.Setup(s => s.IActiveDoc2).Returns(model.Object);
        swApp.Setup(s => s.ActivateDoc(@"C:\Models\SquareTube.sldprt"))
            .Returns(new SwDocumentInfo(@"C:\Models\SquareTube.sldprt", "SquareTube", (int)swDocumentTypes_e.swDocPART));

        var manager = new Mock<ISwConnectionManager>();
        manager.Setup(m => m.IsConnected).Returns(true);
        manager.Setup(m => m.SwApp).Returns(swApp.Object);
        manager.Setup(m => m.EnsureConnected());

        var result = new AssemblyService(manager.Object).OpenComponentForEditing(@"C:\Models\SquareTube.sldprt", "SquareTube-1");

        Assert.True(result.Opened);
        Assert.Equal("SquareTube-1", result.ComponentName);
        Assert.Equal(@"C:\Models\SquareTube.sldprt", result.ComponentPath);
        swApp.Verify(s => s.ActivateDoc(@"C:\Models\SquareTube.sldprt"), Times.Once);
    }
}
